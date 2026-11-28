"""Closed original grantor and finite damage roles across two independent programs."""

from collections.abc import Mapping

from ..granted_activation_model import GRANTOR_REDIRECTION_CAPABILITY, GRANTOR_REDIRECTION_VERSION
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects
from .finite_damage_redirection import _has_free_activation_price

FIELD = "grantor_owned_redirect"
MECHANIC = "grantor-only-finite-redirection"
BODY = "The next 1 damage that would be dealt to target creature, planeswalker, or player this turn is dealt to this creature instead."
QUOTED = "{0}: " + BODY
_TARGET_PREDICATE = "player_or_creature_or_planeswalker"


def grantor_redirect_activation_schema():
    return {"zones": ["player", "battlefield"], "categories": ["player", "permanent"],
            "predicate": _TARGET_PREDICATE, "count": 1}


def grantor_redirect_spell_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
            "controller_relation": "you", "count": 1}


def grantor_redirect_effect():
    return {"op": "create_damage_redirection", FIELD: 1, "source": "$source",
            "subject": "$target.0", "destination": "$source", "maximum_damage": 1}


def grantor_redirect_grant(ability_id, semantic_key, *, activator="$controller", target="$target.0"):
    from ..granted_activation_model import GrantedActivatedAbilitySpec

    fragment = GrantedActivatedAbilitySpec(
        ability_id=ability_id, semantic_key=semantic_key, cost_text="{0}", effect_text=BODY,
        schema_version=GRANTOR_REDIRECTION_VERSION, activation_grantor=activator,
    )
    return {"op": "apply_source_characteristics_until_end_of_turn", "schema_version": 7,
            "card": target, "keywords": [], "ability_fragments": [{"kind": "granted_activated", "value": fragment.to_dict()}],
            "exclude_source": False, "printed_only": False}


def is_grantor_redirect_fragment(fragment):
    return (isinstance(fragment, Mapping) and fragment.get("kind") == "granted_activated"
            and isinstance(fragment.get("value"), Mapping)
            and fragment["value"].get("schema_version") == GRANTOR_REDIRECTION_VERSION)


def grantor_redirect_grant_capabilities(effect, target_schema, cost_schema):
    fragments = tuple(effect.get("ability_fragments") or ())
    marked = tuple(fragment for fragment in fragments if is_grantor_redirect_fragment(fragment))
    if not marked:
        return None
    if len(marked) != 1:
        return ()
    fragment, = marked
    value = fragment["value"]
    if (cost_schema is not None or thaw_value(target_schema) != grantor_redirect_spell_schema()
            or type(target_schema["count"]) is not int or thaw_value(effect) != grantor_redirect_grant(
                value.get("ability_id"), value.get("semantic_key"),
            )):
        return ()
    return (GRANTOR_REDIRECTION_CAPABILITY, "continuous.ability.temporary_quoted_grant",
            "continuous.resolution.fixed_characteristics_until_end_of_turn", "target.revalidate_resolution")


def grantor_redirect_activation_dependencies(program):
    markers = any(FIELD in effect for effect in nested_effects((*program.effects, *program.handlers)))
    if not markers:
        return ()
    if (program.event != "activate" or program.active_zone != "battlefield"
            or not program.ability_id.startswith("ability:granted:")
            or "granted_activated_ability" not in program.coverage or MECHANIC not in program.coverage
            or not _has_free_activation_price(program.cost_schema) or program.event_condition is not None
            or program.handlers or thaw_value(program.target_schema) != grantor_redirect_activation_schema()
            or type(program.target_schema["count"]) is not int
            or tuple(map(FrozenMap, program.effects)) != (FrozenMap(grantor_redirect_effect()),)
            or type(program.effects[0][FIELD]) is not int or type(program.effects[0]["maximum_damage"]) is not int):
        raise ValueError("Grantor-only one-point redirection requires its independently bound free activation")
    return (GRANTOR_REDIRECTION_CAPABILITY, "target.revalidate_resolution")


def is_closed_grantor_redirect_activation(program):
    try:
        required = grantor_redirect_activation_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def validate_grantor_redirect_grant_child(program, lookup):
    if program is None or not any(
        is_grantor_redirect_fragment(fragment)
        for effect in program.effects for fragment in effect.get("ability_fragments", ())
    ):
        return
    if (program.event != "resolve" or program.active_zone != "stack" or program.event_condition is not None
            or program.handlers or len(program.effects) != 1):
        raise ValueError("Grantor-only redirection requires its original resolving grant")
    required = grantor_redirect_grant_capabilities(program.effects[0], program.target_schema, program.cost_schema)
    if not required or not set(required) <= set(program.capability_dependencies):
        raise ValueError("Grantor-only redirection requires its exact creature grant")
    value = program.effects[0]["ability_fragments"][0]["value"]
    child = lookup(value["semantic_key"])
    if child is None or child.oracle_id != program.oracle_id or child.ability_id != value["ability_id"]:
        raise ValueError("Grantor-only redirection lost its independent activation child")
    required = grantor_redirect_activation_dependencies(child)
    if not required or not set(required) <= set(child.capability_dependencies):
        raise ValueError("Grantor-only redirection has an unbound activation child")
