from __future__ import annotations

"""A source's owner creates stock tokens for its named departure counters."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD, fixed_ordinary_token_name
from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec,
)
from ..semantic_runtime.context import semantic_source_context

OWNER_DEPARTURE_TOKEN_CAPABILITY = "token.creation.owner_source_departure_counters"
OWNER_DEPARTURE_TOKEN_FIELD = "owner_departure_counter_token"
OWNER_DEPARTURE_TOKEN_EVENT = "permanent.leave.self"


def owner_departure_token_effect():
    return {
        "op": "create_token",
        OWNER_DEPARTURE_TOKEN_FIELD: 1,
        "controller": "$context.owner",
        "name": fixed_ordinary_token_name("Soldier"),
        "quantity": ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, counter_name="quest", schema_version=2).to_dict(),
        "tapped": True,
        "characteristics": {
            "type_line": "Token Creature — Soldier", TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
            "colors": ["W"], "power": "1", "toughness": "1",
        },
    }


def owner_departure_token_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    effect, = effects
    if (type(effect.get(OWNER_DEPARTURE_TOKEN_FIELD)) is not int
            or effect.get("tapped") is not True
            or FrozenMap(effect) != FrozenMap(owner_departure_token_effect())):
        return ()
    return (OWNER_DEPARTURE_TOKEN_CAPABILITY, "token.creation.fixed_definition",
            SCALAR_AMOUNT_CAPABILITY, "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def owner_departure_token_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    if not contains_nested_key((program.effects, program.handlers), OWNER_DEPARTURE_TOKEN_FIELD):
        return ()
    required = owner_departure_token_node_capabilities(program.effects, program.target_schema)
    if (not required or program.event != OWNER_DEPARTURE_TOKEN_EVENT or program.active_zone != "battlefield"
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Owner token creation requires its own exact source departure trigger")
    return required


def owner_departure_token_payload(host, effect, *, actor):
    """Authenticate the locked departure owner and the stock resolved token."""
    raw_source = effect.get("_runtime_source")
    ref = raw_source.get("stack_ref") if isinstance(raw_source, Mapping) else None
    item = next((item for item in host.state.stack if item.ref == ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True
            or raw_source != semantic_source_context(item, host.state.cards).to_dict()):
        raise GameRuleError("Owner token creation needs its original resolving departure")
    program = host.semantics.get(item.semantic_key)
    try:
        required = owner_departure_token_runtime_dependencies(program) if program is not None else ()
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    owner = item.context.get("owner")
    quantity = effect.get("quantity")
    if (not required or not host.semantic_program_is_current_trusted(program)
            or not set(required) <= set(program.capability_dependencies)
            or type(owner) is not str or owner not in host.state.players
            or type(quantity) is not int or quantity < 0):
        raise GameRuleError("Owner token creation lost its admitted owner or counter quantity")
    expected = {**owner_departure_token_effect(), "controller": owner, "quantity": quantity}
    actual = {key: value for key, value in effect.items()
              if key not in {"reason", "_runtime_source", "_replacement_selections"}}
    if (type(actual.get(OWNER_DEPARTURE_TOKEN_FIELD)) is not int or actual.get("tapped") is not True
            or FrozenMap(actual) != FrozenMap(expected)):
        raise GameRuleError("Owner token creation lost its original controller, definition, or tap result")
    if owner not in host.active_seats or quantity == 0:
        return None
    return {key: value for key, value in effect.items() if key != OWNER_DEPARTURE_TOKEN_FIELD}
