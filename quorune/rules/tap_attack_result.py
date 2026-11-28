"""Typed optional subtype tap and the ordered result of a self-attack."""

from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


OPERATION = "choose_subtype_taps_for_attack_result"
MECHANIC = "subtype-tap-attack-result"
CAPABILITY = "choice.attack.subtype_tap_pump_and_damage"
DEPENDENCIES = (CAPABILITY, "trigger.event.normalized_self_attack", "trigger.placement.apnap",
                "permanent.tap.effect", "continuous.resolution.fixed_characteristics_until_end_of_turn",
                "damage.amount.positive")


def tap_attack_effect(subtype):
    if type(subtype) is not str or canonical_creature_subtype(subtype) != subtype:
        raise ValueError("An attack tap result requires a canonical subtype")
    return {
        "op": OPERATION, "schema_version": 1, "player": "$controller",
        "subtype": subtype, "declaration": "$context.attack_transition",
        "damage_template": {"op": "damage", "source": "$source", "target": "$context.defender", "amount": 1},
    }


def tap_attack_node_capabilities(effects, target_schema, mechanics, cost_schema):
    marked = MECHANIC in mechanics or any(effect.get("op") == OPERATION for effect in effects)
    if not marked:
        return None
    if target_schema is not None or cost_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        if type(effect.get("schema_version")) is int and FrozenMap(effect) == FrozenMap(tap_attack_effect(effect.get("subtype"))):
            return DEPENDENCIES
    except ValueError:
        pass
    return ()


def tap_attack_program_dependencies(program):
    uses = any(effect.get("op") == OPERATION for effect in nested_effects((program.effects, program.handlers)))
    if not uses and MECHANIC not in program.coverage:
        return ()
    owned = tap_attack_node_capabilities(program.effects, program.target_schema, program.coverage, program.cost_schema)
    if (not owned or program.handlers or program.active_zone != "battlefield"
            or program.event != "creature.attacks" or not program.ability_id.startswith("trigger:")
            or FrozenMap(program.event_condition or {}) != FrozenMap({
                "field": "card", "op": "eq", "value": "$source.ref",
            })):
        raise ValueError("Subtype attack payment needs its original untargeted self-attack trigger")
    return owned
