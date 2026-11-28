"""Current monarch state guards the stock named controller-creature doubling."""

from collections.abc import Mapping

from .continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from .counter_names import existing_counter_amount_descriptor, is_existing_counter_amount
from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value


MONARCH_COUNTER_DOUBLING_VERSION = 34
MONARCH_COUNTER_DOUBLING_MECHANICS = ("named-counter-doubling", "cr-122-counters")
MONARCH_COUNTER_DISTRIBUTION_PREFIX_MECHANICS = (
    "cr-122-counters", "cr-115-targets", "quantified-counter-target-set",
)
_CONDITION = FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH)
_CREATURE_SET = FrozenMap({
    "controller_relation": "actor", "target_controller": None, "exclude_source": False,
    "query": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).canonical_dict(),
})


def monarch_counter_doubling_guard():
    return {
        "op": "apply_if_public_condition", "schema_version": MONARCH_COUNTER_DOUBLING_VERSION,
        "player": "$controller", "condition": _CONDITION.to_dict(),
        "effects": [{"op": "place_counters_on_set", "source": "$source", "set": thaw_value(_CREATURE_SET),
                     "counter": "+1/+1", "amount": existing_counter_amount_descriptor()}],
        "mechanic_ids": list(MONARCH_COUNTER_DOUBLING_MECHANICS),
        "prefix_mechanic_ids": list(MONARCH_COUNTER_DISTRIBUTION_PREFIX_MECHANICS),
    }


def is_monarch_counter_doubling(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
            and type(effect.get("schema_version")) is int and effect["schema_version"] == MONARCH_COUNTER_DOUBLING_VERSION)


def validate_monarch_counter_doubling(effect):
    if (not is_monarch_counter_doubling(effect)
            or set(effect) != {"op", "schema_version", "player", "condition", "effects", "mechanic_ids", "prefix_mechanic_ids"}
            or type(effect["player"]) is not str or not effect["player"]
            or not isinstance(effect["condition"], Mapping)
            or FrozenMap(effect["condition"]) != FrozenMap(_CONDITION.to_dict())
            or tuple(effect["mechanic_ids"]) != MONARCH_COUNTER_DOUBLING_MECHANICS
            or tuple(effect["prefix_mechanic_ids"]) != MONARCH_COUNTER_DISTRIBUTION_PREFIX_MECHANICS
            or not isinstance(effect["effects"], (list, tuple)) or len(effect["effects"]) != 1):
        raise ValueError("Monarch counter doubling requires the original distribution and current resolving controller")
    child, = effect["effects"]
    if (not isinstance(child, Mapping) or set(child) != {"op", "source", "set", "counter", "amount"}
            or child["op"] != "place_counters_on_set" or child["counter"] != "+1/+1"
            or not isinstance(child["set"], Mapping) or FrozenMap(child["set"]) != _CREATURE_SET
            or not is_existing_counter_amount(child["amount"])):
        raise ValueError("Monarch doubling requires the exact current named counters on controlled creatures")
    source, player = child["source"], effect["player"]
    if player == "$controller":
        if source != "$source":
            raise ValueError("Monarch doubling requires its original ability source")
    elif player.startswith("$") or type(source) is not str or not source or source.startswith("$"):
        raise ValueError("Monarch doubling requires a resolved source and controller")
    return _CONDITION, (child,)
