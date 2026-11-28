"""Closed resolution snapshot for countered permanents and independent shields."""

from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..replacement.immutable import thaw_value


FIELD = "counter_snapshot_prevention_version"
KEYWORDS = ("Hexproof", "Indestructible")


def counter_snapshot_protection_effect(controller="$controller"):
    return {
        "op": "modify_all_matching_permanents_until_end_of_turn",
        "predicate": ObjectQuerySpec(
            zones=("battlefield",), controller=controller,
            state_predicate=PermanentStatePredicateSpec(minimum_total_counter_count=1),
        ).to_dict(),
        "power": 0, "toughness": 0, "keywords": list(KEYWORDS), FIELD: 1,
    }


def counter_snapshot_protection_query(effect, *, controller="$controller"):
    public = {key: value for key, value in effect.items() if key != "_runtime_source"}
    if (
        type(public.get(FIELD)) is not int
        or type(public.get("power")) is not int or type(public.get("toughness")) is not int
        or thaw_value(public) != counter_snapshot_protection_effect(controller)
    ):
        raise ValueError("Counter snapshot protection requires the complete controlled permanent predicate")
    return ObjectQuerySpec.from_dict(public["predicate"])
