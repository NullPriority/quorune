"""Compute direct noncombat damage excess from committed recipient facts."""

from dataclasses import dataclass

from .errors import GameRuleError
from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap
from .rules.committed_quantity_followups import CommittedQuantityFollowup


EXCESS_TRAMPLE_FIELD = "committed_excess_power_and_trample_version"
EXCESS_TRAMPLE_CAPABILITY = "damage.result.committed_excess_power_and_trample"
EXCESS_TRAMPLE_MECHANIC = "committed-excess-power-and-trample"
EXCESS_TRAMPLE_TEMPLATE = "damage-targeted-creature-committed-excess-power-and-trample-v1"


@dataclass(frozen=True, slots=True)
class RecipientExcessDamage:
    object_id: str
    logical_object_id: str
    amount: int


def committed_recipient_excess(host, events):
    """Read the post-prevention, pre-damage state for each finalized recipient."""
    groups = {}
    for event in events:
        if event.target_kind == "permanent" and event.dealt_amount:
            groups.setdefault((event.target_object_id, event.target_logical_object_id), []).append(event)
    results = []
    for (object_id, logical_id), rows in groups.items():
        card = host.state.cards.get(object_id)
        if card is None or card.zone != "battlefield" or card.logical_object_id != logical_id:
            raise ValueError("Committed damage excess lost its original recipient")
        data = host._effective_card_data(card)
        types, _, _ = host._type_parts(str(data.get("type_line") or ""))
        thresholds = []
        if "creature" in types:
            remaining = max(0, host._numeric_stat(object_id, "toughness") - card.marked_damage)
            if card.deathtouch_damage:
                remaining = 0
            elif any("deathtouch" in row.source_keywords for row in rows):
                remaining = min(remaining, 1)
            thresholds.append(remaining)
        if "planeswalker" in types:
            thresholds.append(card.counters.get("loyalty", 0))
        if "battle" in types:
            thresholds.append(card.counters.get("defense", 0))
        if thresholds:
            amount = max(0, sum(row.dealt_amount for row in rows) - min(thresholds))
            results.append(RecipientExcessDamage(object_id, logical_id, amount))
    return tuple(results)


def excess_trample_followup(actor, amount):
    if type(amount) is not int or amount < 0:
        raise GameRuleError("Committed excess must be a nonnegative quantity")
    return CommittedQuantityFollowup((FrozenMap({
        "op": "modify_all_matching_permanents_until_end_of_turn",
        "predicate": ObjectQuerySpec(
            zones=("battlefield",), controller=actor, types_all=("creature",),
        ).to_dict(),
        "power": amount, "toughness": 0, "keywords": ["Trample"],
    }),))
