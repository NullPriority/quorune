from __future__ import annotations

"""Trigger once per paid mana unit after a qualifying cast becomes committed."""

from collections.abc import Sequence

from ...commander import current_commander_objects
from ...creature_subtypes import CREATURE_SUBTYPES
from ...mana_spend_rules import CommanderScryMana, decode_commander_scry_mana
from ...model import StackItem
from ..mana_pool_payment import ManaPoolAllocation
from ..spell_cast_events import SpellCastEvent


def _commander_creature_types(host, controller: str) -> set[str]:
    result = set()
    for card in current_commander_objects(host.state, controller):
        data = host._effective_card_data(card)
        types, subtypes, _ = host._type_parts(str(data.get("type_line") or ""))
        if types & {"creature", "kindred"}:
            result.update(subtypes & CREATURE_SUBTYPES)
    return result


def _scry_trigger(host, source: CommanderScryMana) -> StackItem:
    ref = host._next_ref("S")
    return StackItem(
        stack_id=host._stable_runtime_id("stack", ref),
        ref=ref,
        kind="triggered_ability",
        controller=source.controller,
        label="Scry 1 from mana spent to cast",
        source_object_id=source.source_object_id,
        visibility=list(host.seats),
        context={
            "event": "spell.cast",
            "source_logical_object_id": source.source_logical_object_id,
            "dynamic_effects": [{"op": "scry", "player": "$controller", "count": 1}],
        },
    )


def spent_mana_cast_triggers(
    host,
    event: SpellCastEvent,
    payments: Sequence[ManaPoolAllocation],
) -> list[StackItem]:
    """Read effective commanders and the actual spell once the cast is committed."""
    if "creature" not in event.types or not event.subtypes:
        return []
    spell_types = set(event.subtypes) & CREATURE_SUBTYPES
    shared_by_controller = {}
    triggers = []
    for payment in payments:
        for unit, count in payment.units:
            rider = decode_commander_scry_mana(unit.restriction)
            if rider is None:
                continue
            if rider.controller not in shared_by_controller:
                shared_by_controller[rider.controller] = bool(
                    spell_types & _commander_creature_types(host, rider.controller)
                )
            if shared_by_controller[rider.controller]:
                triggers.extend(_scry_trigger(host, rider) for _ in range(count))
    return triggers
