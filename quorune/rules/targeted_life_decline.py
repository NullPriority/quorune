"""One optional fixed life price and one bound whole-hand discard result."""

from collections.abc import Mapping

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..replacement.immutable import thaw_value
from ..whole_hand_discard_model import WHOLE_HAND_DISCARD_MECHANIC, WHOLE_HAND_DISCARD_OPERATION
from .whole_hand_discard_shapes import whole_hand_discard_node_capabilities

TARGETED_LIFE_DECLINE_CAPABILITY = "effect.choice.target_player_fixed_life_decline"
TARGET_PLAYER_SCHEMA = {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}
_TARGET_DEPS = (WHOLE_HAND_DISCARD_MECHANIC, "cr-402-hand", "cr-115-targets")
_WRAPPER_FIELDS = {"op", "schema_version", "player", "payment", "effects", "declined_effects"}


def is_targeted_life_decline_effect(effect):
    return (
        isinstance(effect, Mapping)
        and effect.get("op") == "offer_optional_mana_payment"
        and effect.get("schema_version") == 2 and "declined_effects" in effect
        and isinstance(effect.get("payment"), Mapping) and effect["payment"].get("kind") == "life"
    )


def targeted_life_decline_parts(effect):
    if (not is_targeted_life_decline_effect(effect) or set(effect) != _WRAPPER_FIELDS
            or type(effect["schema_version"]) is not int
            or not isinstance(effect["effects"], (list, tuple)) or effect["effects"]
            or not isinstance(effect["declined_effects"], (list, tuple)) or len(effect["declined_effects"]) != 1):
        raise ValueError("Fixed life decline requires one closed alternative and no paid result")
    cost = FixedEffectPaymentSpec.from_dict(effect["payment"])
    result = effect["declined_effects"][0]
    if (cost.amount < 1 or type(effect["player"]) is not str or not effect["player"]
            or not isinstance(result, Mapping)
            or set(result) != {"op", "actor", "players"} or result["op"] != WHOLE_HAND_DISCARD_OPERATION
            or type(result["actor"]) is not str or not result["actor"]
            or not isinstance(result["players"], (list, tuple))
            or tuple(result["players"]) != (effect["player"],)):
        raise ValueError("Fixed life decline must discard only the paying player's whole hand")
    return cost, result


def targeted_life_decline_node_capabilities(effect, target_schema, mechanic_ids):
    if (FIXED_EFFECT_PAYMENT_MECHANIC not in mechanic_ids
            or not isinstance(target_schema, Mapping) or thaw_value(target_schema) != TARGET_PLAYER_SCHEMA
            or type(target_schema["count"]) is not int or not isinstance(effect, Mapping)
            or effect.get("player") != "$target.0"):
        return ()
    try:
        cost, result = targeted_life_decline_parts(effect)
    except (TypeError, ValueError, KeyError):
        return ()
    known_result = whole_hand_discard_node_capabilities(
        effects=(thaw_value(result),), target_schema=TARGET_PLAYER_SCHEMA, mechanic_ids=_TARGET_DEPS,
    )
    return tuple(sorted({TARGETED_LIFE_DECLINE_CAPABILITY, *cost.capabilities, *known_result})) if known_result else ()


def targeted_life_decline_payload_dependencies(effect):
    required = targeted_life_decline_node_capabilities(
        effect, TARGET_PLAYER_SCHEMA, (FIXED_EFFECT_PAYMENT_MECHANIC,),
    )
    if not required:
        raise ValueError("Fixed life decline has no bound target-player authority")
    return required
