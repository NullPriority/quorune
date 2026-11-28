"""An untargeted controller choice feeding one simultaneous token instruction."""

from collections.abc import Mapping

from ..fixed_token_production import (
    FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID, FIXED_DISTRIBUTED_TOKEN_OPERATION, FixedTokenCreatorScope,
)
from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects

PAIRED_TOKEN_FIELD = "paired_token_creators"
PAIRED_TOKEN_STATS = "stats"
PAIRED_TOKEN_QUANTITY = "quantity"
_FIELDS = frozenset({"op", "player", PAIRED_TOKEN_FIELD, "token", "x", "x_use"})


def uses_paired_token_creators(effect):
    return isinstance(effect, Mapping) and PAIRED_TOKEN_FIELD in effect


def paired_token_choice_effect(token, x_use):
    return {"op": "choose_option", "player": "$controller", PAIRED_TOKEN_FIELD: 1,
            "token": token, "x": CastXAmountSpec().to_dict(), "x_use": x_use}


def paired_token_inputs(effect, *, actor="$controller"):
    from .token_creation_capability_shapes import _fixed_token_effect_closure

    if (set(effect) != _FIELDS or effect["op"] != "choose_option" or effect["player"] != actor
            or type(effect[PAIRED_TOKEN_FIELD]) is not int or effect[PAIRED_TOKEN_FIELD] != 1):
        raise ValueError("Paired token creation requires its exact controller choice")
    amount, use, token = effect["x"], effect["x_use"], thaw_value(effect["token"])
    if not isinstance(token, dict):
        raise ValueError("Paired token creation requires a token definition")
    if actor == "$controller":
        if CastXAmountSpec.from_dict(amount) != CastXAmountSpec():
            raise ValueError("Paired token creation requires the unscaled cast X")
    elif type(amount) is not int or amount < 0:
        raise ValueError("Paired token creation requires a bound nonnegative X")
    if token.get("controller") not in (actor, "$controller"):
        raise ValueError("Paired token definition changed its controller")
    token["controller"] = "$controller"
    closure = _fixed_token_effect_closure(token)
    if (closure is None or token["quantity"] != 1 or set(token["characteristics"]) != {
            "type_line", "colors", "power", "toughness", "literal_token_name_version",
        } or "tapped" in token or not token["characteristics"]["type_line"].startswith("Token Creature — ")):
        raise ValueError("Paired token creation requires one ordinary creature token definition")
    if use == PAIRED_TOKEN_STATS:
        if (token["characteristics"]["power"], token["characteristics"]["toughness"]) != ("2", "2"):
            raise ValueError("Paired token size requires the closed X/X projection")
    elif use != PAIRED_TOKEN_QUANTITY:
        raise ValueError("Paired token creation requires its cast-X result slot")
    return token, amount, use, closure


def paired_token_choice_dependencies(effect):
    _, _, _, (owned, _) = paired_token_inputs(effect)
    return (*owned, FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID, DECLARED_AMOUNT_CAPABILITY)


def paired_token_node_capabilities(effects, target_schema, cost_schema):
    if target_schema is not None or cost_schema is not None or len(effects) != 2:
        return ()
    try:
        first, second = effects
        if first.get("x_use") != PAIRED_TOKEN_STATS or second.get("x_use") != PAIRED_TOKEN_QUANTITY:
            return ()
        return tuple(sorted({cap for effect in effects for cap in paired_token_choice_dependencies(effect)}))
    except (AttributeError, KeyError, TypeError, ValueError):
        return ()


def paired_token_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_paired_token_creators(effect))
    if not marked:
        return ()
    if (tuple(program.effects) != marked or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Paired token choices require their complete untargeted spell")
    required = paired_token_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if not required:
        raise ValueError("Paired token choices require both ordered cast-X instructions")
    return required


def is_closed_paired_token_program(program):
    try:
        required = paired_token_program_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def paired_token_result(token, amount, use, opponent):
    if use == PAIRED_TOKEN_STATS:
        token["characteristics"].update(power=str(amount), toughness=str(amount))
    elif amount == 0:
        return ()
    else:
        token["quantity"] = amount
    return ({
        "op": FIXED_DISTRIBUTED_TOKEN_OPERATION,
        "controller": "$controller",
        "scope": FixedTokenCreatorScope.CONTROLLER_AND_CHOSEN_OPPONENT.value,
        "opponent": opponent,
        "token": token,
    },)
