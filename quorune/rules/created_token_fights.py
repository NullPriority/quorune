from __future__ import annotations

from collections.abc import Mapping

from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_CAPABILITY
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


FIELD = "created_token_fights"
CAPABILITY = "token.creation.fixed_opponent_fights"
_DEPENDENCIES = (
    CAPABILITY, CREATURE_POWER_DAMAGE_CAPABILITY, "damage.batch.fixed_set",
    "damage.amount.positive", "damage.result.multitype_permanent",
    "token.creation.fixed_definition", "token.creation.quantity_replacement",
)


def created_token_fight_effect(definition):
    return {
        "op": "choose_cards_apnap", FIELD: 1,
        "token": {"name": definition["name"], "characteristics": thaw_value(definition["characteristics"])},
    }


def uses_created_token_fights(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_cards_apnap" and FIELD in effect


def created_token_fight_payload_dependencies(effect):
    if not uses_created_token_fights(effect):
        return ()
    from .token_creation_capability_shapes import _fixed_token_effect_closure

    if (set(effect) != {"op", FIELD, "token"} or type(effect.get(FIELD)) is not int or effect[FIELD] != 1
            or not isinstance(effect["token"], Mapping) or set(effect["token"]) != {"name", "characteristics"}):
        raise ValueError("A created-token fight requires the closed fixed creature definition")
    token = {"op": "create_token", "controller": "$controller", "quantity": 1, **thaw_value(effect["token"])}
    closed = _fixed_token_effect_closure(token)
    line = str(token["characteristics"].get("type_line", "")) if isinstance(token["characteristics"], Mapping) else ""
    if closed is None or "creature" not in line.casefold().split(" \u2014 ")[0].split():
        raise ValueError("Only an independently closed creature token can be created to fight")
    return tuple(sorted({*_DEPENDENCIES, *closed[0]}))


def created_token_fight_node_capabilities(effects, target_schema, cost_schema):
    if not any(uses_created_token_fights(effect) for effect in effects):
        return None
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        return created_token_fight_payload_dependencies(effects[0])
    except (ValueError, TypeError, KeyError):
        return ()


def created_token_fight_program_dependencies(program):
    if not any(uses_created_token_fights(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = created_token_fight_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if not required or program.handlers or program.event_condition is not None or (program.event, program.active_zone) != ("resolve", "stack"):
        raise ValueError("Created-token fights require one complete untargeted spell")
    return required
