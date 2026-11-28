"""Close a public opponent opt-in for one ordered draw and token pair."""

from collections.abc import Mapping

from ..fixed_token_production import FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID, FIXED_DISTRIBUTED_TOKEN_OPERATION
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


FIELD = "tempt_draw_token_version"
CAPABILITY = "choice.opponents.tempt_draw_and_token"
MECHANIC = "tempting-offer-draw-token"
ORDER = "tempt_offer_order"
ANSWERS = "tempt_offer_answers"
CREATORS = "tempt_accepted_creators"
STAGE_FIELDS = ("player", ORDER, ANSWERS)


def offer_effect(token):
    return {"op": "offer_draw", FIELD: 1, "beneficiary": "$controller", "token": token}


def draw_result(actor):
    return {"op": "draw", "player": actor, "count": 1, "private": True}


def token_result(token, actor):
    return {**token, "controller": actor}


def validate_offer(effect, *, materialized=False):
    if not isinstance(effect, Mapping) or type(effect.get(FIELD)) is not int or effect[FIELD] != 1:
        raise ValueError("Tempting offer requires its exact draw-and-token version")
    fields = {"op", FIELD, "beneficiary", "token"}
    if materialized and set(STAGE_FIELDS) <= effect.keys():
        fields.update(STAGE_FIELDS)
    if set(effect) != fields or effect["op"] != "offer_draw":
        raise ValueError("Tempting offer has unsupported fields")
    beneficiary = effect["beneficiary"]
    if materialized:
        if type(beneficiary) is not str or not beneficiary or beneficiary.startswith("$"):
            raise ValueError("Tempting offer requires the actual resolving controller")
    elif beneficiary != "$controller":
        raise ValueError("Tempting offer must repeat for the resolving controller")
    token = thaw_value(effect["token"])
    if (not isinstance(token, dict) or token.get("op") != "create_token"
            or token.get("controller") != beneficiary or type(token.get("quantity")) is not int
            or token["quantity"] != 1 or set(token) != {"op", "name", "controller", "quantity", "characteristics"}):
        raise ValueError("Tempting offer needs one ordinary fixed token")
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities, token_creation_component_mechanics

    normalized = token_result(token, "$controller")
    mechanics = token_creation_component_mechanics((normalized,), {"cr-111-tokens"})
    required = fixed_token_creation_node_capabilities(
        effects=(normalized,), target_schema=None, mechanic_ids=set(mechanics),
    )
    if not required:
        raise ValueError("Tempting offer needs a capability-closed token definition")
    return (CAPABILITY, "zone.draw.library_to_hand", FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID, *required)


def all_effects(token):
    return draw_result("$controller"), token, offer_effect(token)


def offer_node_capabilities(*, effects, target_schema, mechanic_ids, **_):
    if (target_schema is not None or MECHANIC not in mechanic_ids or len(effects) != 3
            or FIELD not in effects[2]):
        return ()
    try:
        required = validate_offer(effects[2])
        if FrozenMap({"effects": effects}) != FrozenMap({"effects": all_effects(effects[2]["token"])}):
            return ()
    except (TypeError, ValueError, KeyError):
        return ()
    return required


def offer_runtime_dependencies(program):
    if not any(FIELD in effect for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = offer_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Tempting offer requires the complete ordered resolving spell")
    return required


def opponent_result_token_effect(actor, token, recipients):
    return {"op": FIXED_DISTRIBUTED_TOKEN_OPERATION, "controller": actor,
            "token": token, CREATORS: tuple(recipients)}


def resolved_tempt_creators(effect, actor, apnap):
    if set(effect) != {"op", "controller", "token", CREATORS}:
        raise ValueError("Tempting offer token batch changed its sealed creator fields")
    recipients = effect[CREATORS]
    if (not isinstance(recipients, (list, tuple)) or not recipients or any(type(seat) is not str for seat in recipients)
            or tuple(recipients) != tuple(seat for seat in apnap if seat in recipients and seat != actor)):
        raise ValueError("Tempting offer token batch requires ordered active accepting opponents")
    return tuple(recipients)
