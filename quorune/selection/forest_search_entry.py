from __future__ import annotations

"""One private search selection, one simultaneous Forest entry batch."""

from itertools import combinations

from ..errors import GameRuleError
from ..life_state import LifeStateError, prepare_life_payment
from ..zone_transitions import ZoneTransitionOwner


FOREST_ENTRY_PAID_REFS = "forest_entry_paid_refs"


def _entry_price(host, card):
    record = host.card_record(card)
    return host._land_entry_life_amount(record) if record is not None else 0


def _valid_payment_groups(host, seat, priced):
    yield ()
    for count in range(1, len(priced) + 1):
        for group in combinations(priced, count):
            amount = sum(price for _, price in group)
            try:
                prepare_life_payment(host, seat, amount)
            except LifeStateError:
                continue
            yield group


def begin_forest_entry_payment_choice(host, context, selected_refs, *, choice_id):
    if FOREST_ENTRY_PAID_REFS in context.response:
        raise GameRuleError("Forest entry payments require the private entry choice")
    selected = [host._resolve_object(context.seat, ref, zones={"library"}, owned_only=True)
                for ref in selected_refs]
    priced = [(card, price) for card in selected if (price := _entry_price(host, card)) > 0]
    payment_groups = tuple(_valid_payment_groups(host, context.seat, priced))
    if len(payment_groups) == 1:
        return False
    options, branches = [], {}
    for index, payment in enumerate(payment_groups):
        option = f"entry-{index}"
        names = ", ".join(card.printed_name for card, _ in payment)
        count = sum(price for _, price in payment)
        options.append({"id": option, "label": f"Pay {count} life for {names}" if count else "Pay no life"})
        branches[option] = [{
            **context.effect, "_search_completion": {
                "actor": context.seat, "choice_id": choice_id, "selected_refs": list(selected_refs),
                FOREST_ENTRY_PAID_REFS: [card.ref for card, _ in payment],
            },
        }]
    host._begin_semantic_choice(
        item=context.item, effect={"op": "choose_option", "player": context.seat,
            "prompt": "Choose which Forests to pay life for as they enter.",
            "options": options, "then_by_choice": branches},
        remaining=context.continuation["remaining"],
        destination=context.continuation.get("destination"),
        note=str(context.continuation.get("note") or ""),
        instruction_pointer=int(context.frame["instruction_pointer"]),
    )
    return True


def forest_entry_payment_refs(value, selected_refs):
    if (not isinstance(value, (list, tuple)) or any(type(ref) is not str for ref in value)
            or len(value) != len(set(value)) or not set(value) <= set(selected_refs)):
        raise GameRuleError("Forest entry payments require the original selected cards")
    return tuple(value)


def move_up_to_two_forests(host, selected, *, seat, reason, destination, response, replacement_selections):
    if destination != "battlefield" or "entry_pay_life" in response or "pay_life" in response:
        raise GameRuleError("Forest search entry replacements belong to the selected cards")
    if not selected:
        return []
    controllers = {card.object_id: seat for card in selected}
    paid = forest_entry_payment_refs(response.get(FOREST_ENTRY_PAID_REFS, ()), (card.ref for card in selected))
    pay_cards = [card for card in selected if card.ref in paid]
    if any(not _entry_price(host, card) for card in pay_cards):
        raise GameRuleError("A paid Forest no longer has its original optional entry price")
    return ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, destination) for card in selected),
        reason=reason, log=False, destination_controllers=controllers, putting_players=controllers,
        entry_pay_life={card.object_id: True for card in pay_cards}, replacement_selections=replacement_selections,
    )


def forest_entry_replacement_is_private(required, *, seat, selected_refs):
    events = required.batch.events
    return (
        required.pending.choice.chooser == seat
        and {event.payload.get("object_ref") for event in events} == set(selected_refs)
        and all(event.kind == "zone.change" and "land" in event.payload.get("object_types", ()) for event in events)
    )
