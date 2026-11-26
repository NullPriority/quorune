from __future__ import annotations

"""The bounded shape of a chapter's resolution-time self-grant."""

from collections.abc import Mapping

from ..ability_fragments import GrantedActivatedAbilitySpec, ability_fragment_from_dict, ability_fragment_to_dict
from .effect_shape_walkers import nested_effects


SAGA_CHAPTER_GRANT_CAPABILITY = "continuous.ability.battlefield_incarnation_grant"
SAGA_CHAPTER_GRANT_MECHANIC = "fixed-saga-chapter-activation-grant"
SAGA_CHAPTER_GRANT_VERSION = 2
_SOURCE = "$source.zone_object"
_OP = "grant_ability_fragment"


def validate_saga_chapter_grant(effect: Mapping, *, resolved: bool = False) -> GrantedActivatedAbilitySpec:
    if (
        not isinstance(effect, Mapping)
        or set(effect) != {"op", "schema_version", "source", "fragment"}
        or effect["op"] != _OP
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != SAGA_CHAPTER_GRANT_VERSION
    ):
        raise ValueError("A chapter grant requires one typed ability and its original battlefield source.")
    source = effect["source"]
    if resolved:
        if source is not None and (type(source) is not str or not source or source.startswith("$")):
            raise ValueError("The original battlefield source must be a resolved reference.")
    elif source != _SOURCE:
        raise ValueError("A chapter may grant abilities only to its original battlefield source.")
    fragment = ability_fragment_from_dict(effect["fragment"])
    if (
        not isinstance(fragment, GrantedActivatedAbilitySpec)
        or fragment.schema_version != 1
        or not fragment.tap_source
        or fragment.sorcery_speed
        or set(symbol for symbol, _ in fragment.mana) - {"GENERIC"}
    ):
        raise ValueError("A chapter grant requires a fixed tap activation with an optional generic mana cost.")
    return fragment


def saga_chapter_grant_effect(fragment: Mapping) -> dict:
    effect = {"op": _OP, "schema_version": SAGA_CHAPTER_GRANT_VERSION, "source": _SOURCE, "fragment": fragment}
    typed = validate_saga_chapter_grant(effect)
    return {**effect, "fragment": ability_fragment_to_dict(typed)}


def saga_chapter_grant_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if (
        SAGA_CHAPTER_GRANT_MECHANIC not in mechanic_ids or target_schema is not None
        or cost_schema is not None or len(effects) != 1
    ):
        return ()
    try:
        validate_saga_chapter_grant(effects[0])
    except (TypeError, ValueError):
        return ()
    return (SAGA_CHAPTER_GRANT_CAPABILITY,)


def saga_chapter_grant_runtime_dependencies(program):
    versioned_grants = tuple(
        effect for effect in nested_effects(program.effects) if effect.get("op") == _OP and "schema_version" in effect
    )
    if not versioned_grants:
        return ()
    prefix, _, chapter_number = program.event.rpartition(".")
    if (
        tuple(program.effects) != versioned_grants or len(versioned_grants) != 1
        or prefix != "saga.chapter" or not chapter_number.isascii() or not chapter_number.isdecimal()
        or chapter_number.startswith("0") or program.active_zone != "battlefield"
        or program.target_schema is not None or program.cost_schema is not None
        or program.event_condition is not None or program.handlers
    ):
        raise ValueError("A battlefield-incarnation grant requires one original untargeted Saga chapter.")
    validate_saga_chapter_grant(versioned_grants[0])
    return (SAGA_CHAPTER_GRANT_CAPABILITY, "counter.producer.saga_lore", "trigger.placement.apnap")
