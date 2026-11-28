from __future__ import annotations

"""Locked attacking creatures and one committed Food entry look-back."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource, matching_battlefield_objects
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..public_history_query_model import controller_food_entry_query, matching_public_history_events
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import contains_nested_key


ATTACK_FOOD_STATS_VERSION_FIELD = "attack_food_stats_version"
ATTACK_FOOD_STATS_CAPABILITY = "continuous.resolution.attacking_stats_food_entry"
ATTACK_FOOD_STATS_MECHANICS = ("cr-611-continuous-effects", "tap-and-untap")
ATTACK_FOOD_SOURCE_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
_ATTACKING_CREATURES = ObjectQuerySpec(
    zones=("battlefield",),
    types_all=("creature",),
    state_predicate=PermanentStatePredicateSpec(attacking=True),
)
_FOOD_ENTRY = controller_food_entry_query()


def attack_food_stats_effect() -> dict:
    return {
        "op": "modify_all_matching_permanents_until_end_of_turn",
        ATTACK_FOOD_STATS_VERSION_FIELD: 1,
        "predicate": _ATTACKING_CREATURES.to_dict(),
        "power": 1,
        "toughness": 1,
        "food_followup": {"entry_query": _FOOD_ENTRY.to_dict(), "untap": True, "power": 2, "toughness": 2},
    }


def validate_attack_food_stats_effect(effect: Mapping, *, resolved: bool = False) -> None:
    if not isinstance(effect, Mapping):
        raise ValueError("Attacking creature stats require a typed instruction")
    ignored = {"reason", "_runtime_source"} if resolved else set()
    material = {key: entry for key, entry in effect.items() if key not in ignored}
    rider = material.get("food_followup")
    if (
        FrozenMap(material) != FrozenMap(attack_food_stats_effect())
        or type(material[ATTACK_FOOD_STATS_VERSION_FIELD]) is not int
        or any(type(material[key]) is not int for key in ("power", "toughness"))
        or any(type(rider[key]) is not int for key in ("power", "toughness"))
        or rider["untap"] is not True
        or type(rider["entry_query"]["schema_version"]) is not int
    ):
        raise ValueError("Attacking creature stats require their exact Food-entry condition and results")


def attack_food_stats_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if len(effects) != 1 or target_schema is not None or not set(ATTACK_FOOD_STATS_MECHANICS) <= set(mechanic_ids):
        return ()
    try:
        validate_attack_food_stats_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    return (
        ATTACK_FOOD_STATS_CAPABILITY,
        "continuous.resolution.fixed_characteristics_until_end_of_turn",
        "permanent.tap_state.fixed_set",
        "trigger.event.normalized_public_action",
        "trigger.placement.apnap",
        *_FOOD_ENTRY.capability_dependencies,
    )


def attack_food_stats_program_dependencies(program):
    if not contains_nested_key((program.effects, program.handlers), ATTACK_FOOD_STATS_VERSION_FIELD):
        return ()
    dependencies = attack_food_stats_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (
        not dependencies or program.handlers or program.cost_schema is not None or program.trigger_limit is not None
        or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
        or FrozenMap(program.event_condition or {}) != FrozenMap(ATTACK_FOOD_SOURCE_CONDITION)
    ):
        raise ValueError("Attacking creature stats require their original untargeted source attack")
    return dependencies


def resolve_attack_food_stats(host, effect, *, actor):
    """Read one source-bound Food fact and one current attacking snapshot."""
    validate_attack_food_stats_effect(effect, resolved=True)
    source = ResolutionEffectSource.from_effect(effect)
    trigger = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (
        trigger is None or trigger.kind != "triggered_ability" or trigger.controller != actor
        or trigger.context.get("currently_resolving") is not True
    ):
        raise ValueError("Attacking creature stats need the current resolving controller")
    program = host.semantics.get(trigger.semantic_key)
    if program is None or not attack_food_stats_program_dependencies(program):
        raise ValueError("Attacking creature stats lost their original attack program")
    facts = matching_public_history_events(host.state, actor, _FOOD_ENTRY)
    if facts is None:
        raise ValueError("The committed Food entry history is unavailable")
    return matching_battlefield_objects(host, _ATTACKING_CREATURES), bool(facts)
