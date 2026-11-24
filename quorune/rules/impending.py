"""The paid Impending choice on a resolving spell or spell copy."""

from __future__ import annotations

from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
    FixedPublicStateFact,
)
from ..entry_counter_model import EffectEntryCounter
from ..errors import StateInvariantError
from ..impending_model import FixedManaImpendingSpec, PAID_IMPENDING_FIELD


IMPENDING_ENTRY_RULE = "702.176a"


def resolving_impending_spec(item, card) -> FixedManaImpendingSpec | None:
    if PAID_IMPENDING_FIELD not in item.context:
        return None
    if (
        item.kind not in {"spell", "spell_copy"}
        or card is None
        or card.zone != "stack"
        or card.object_id != item.card_object_id
        or card.controller != item.controller
    ):
        raise StateInvariantError("Impending payment requires the resolving stack incarnation")
    return FixedManaImpendingSpec.from_dict(item.context[PAID_IMPENDING_FIELD])


def impending_entry_counters(item, card, destination: str) -> tuple[EffectEntryCounter, ...]:
    if destination != "battlefield":
        return ()
    spec = resolving_impending_spec(item, card)
    if spec is None:
        return ()
    return (EffectEntryCounter("time", spec.time_counters, item.controller, card.ref, IMPENDING_ENTRY_RULE),)


def has_paid_impending_time_counter(card) -> bool:
    """Read this permanent's current payment and counter state."""
    if card.zone != "battlefield" or card.phased_out or PAID_IMPENDING_FIELD not in card.annotations:
        return False
    FixedManaImpendingSpec.from_dict(card.annotations[PAID_IMPENDING_FIELD])
    return card.counters.get("time", 0) > 0


def impending_time_counter_condition() -> FixedPublicStateConditionSpec:
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST, amount=1, schema_version=4,
        fact=FixedPublicStateFact.SOURCE_PAID_IMPENDING_WITH_TIME_COUNTER,
    )


def impending_end_step_condition() -> dict[str, object]:
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "end_step"},
        {
            "field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy", "value": True,
            "condition": impending_time_counter_condition().to_dict(),
        },
    ]}


def impending_end_step_effect() -> dict[str, object]:
    return {
        "op": "remove_counters", "card": "$source.zone_object", "counter": "time",
        "amount": 1, "source": "$source",
    }
