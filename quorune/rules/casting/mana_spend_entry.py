from __future__ import annotations

"""Bind a commander entry-counter rider to the actual paid spell."""

from collections.abc import Mapping, Sequence

from ...commander_cast_counts import commander_cast_count
from ...entry_counter_model import EffectEntryCounter
from ...mana_spend_rules import CommanderEntryCounterMana
from ..mana_pool_payment import ManaPoolAllocation


_ENTRY_CONTEXT = "commander_mana_entry_v1"


def bind_paid_commander_entry(
    state, item, card, payments: Sequence[ManaPoolAllocation],
) -> None:
    if not card.is_commander or card.owner != item.controller or item.default_destination != "battlefield":
        return
    units = 0
    for payment in payments:
        for unit, count in payment.units:
            rider = CommanderEntryCounterMana.decode(unit.restriction)
            if rider is not None and rider.controller == item.controller:
                units += count
    if not units:
        return
    amount = units * commander_cast_count(state, item.controller, card.oracle_id)
    if amount:
        item.context[_ENTRY_CONTEXT] = {
            "logical_object_id": card.logical_object_id,
            "controller": item.controller,
            "amount": amount,
        }


def paid_commander_entry_counters(item, card, destination: str) -> tuple[EffectEntryCounter, ...]:
    if item.kind == "spell_copy" or destination != "battlefield" or _ENTRY_CONTEXT not in item.context:
        return ()
    value = item.context[_ENTRY_CONTEXT]
    if (
        not isinstance(value, Mapping)
        or set(value) != {"logical_object_id", "controller", "amount"}
        or item.kind != "spell"
        or item.card_object_id != card.object_id
        or card.zone != "stack"
        or value["logical_object_id"] != card.logical_object_id
        or value["controller"] != item.controller
        or not card.is_commander
        or card.owner != item.controller
        or type(value["amount"]) is not int
        or value["amount"] < 1
    ):
        raise ValueError("Paid commander entry requires its original cast object and positive counter amount")
    return (EffectEntryCounter(
        "+1/+1", value["amount"], item.controller, card.ref, "614.1c", additional=True,
    ),)
