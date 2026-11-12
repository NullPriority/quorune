from __future__ import annotations

"""Resolution-scoped disclosure and casting for the exact cards drawn."""

from dataclasses import replace

from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError, StateInvariantError
from ..replacement.immutable import thaw_value
from ..rules.casting.face_queries import offered_cast_methods
from ..rules.casting.proposal import build_cast_offer
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.resolving_cast_payments import commit_resolving_cast
from .captured_hand import (
    CAPTURED_DRAW_CAST_FIELD,
    CAPTURED_HAND_CONTEXT,
    DRAW_CAPTURE_FINISHED,
    CapturedDrawnHand,
    captured_draw_cast_instruction,
    captured_draw_cast_offer,
)


def _live_item(host, actor, stack_ref):
    item = next((row for row in host.state.stack if row.ref == stack_ref), None)
    if (item is None or item.controller != actor or actor not in host.active_seats
            or not item.context.get("currently_resolving")):
        raise GameRuleError("The drawn-card cast lost its original resolution")
    return item


def _capture(item, count):
    raw = item.context.get(CAPTURED_HAND_CONTEXT)
    hand = CapturedDrawnHand(count) if raw is None else CapturedDrawnHand.from_dict(raw)
    if hand.count != count:
        raise GameRuleError("The resolving draw changed its fixed count")
    return hand


def _current_hand_card(state, actor, identity):
    card = state.cards.get(identity.object_id)
    if (card is not None and card.zone == "hand" and card.owner == actor
            and card.ref == identity.ref and card.logical_object_id == identity.logical_object_id
            and card.object_id in state.players[actor].zones["hand"]):
        return card
    return None


def capture_drawn_hand_card(host, *, actor, card, action):
    item = _live_item(host, actor, action.stack_ref)
    hand = _capture(item, action.count)
    identity = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
    if hand.revealed or _current_hand_card(host.state, actor, identity) is None:
        raise GameRuleError("Draw capture requires the current unrevealed hand incarnation")
    item.context[CAPTURED_HAND_CONTEXT] = replace(hand, cards=(*hand.cards, identity)).to_dict()


def bind_captured_draw_offer(item, effect):
    if CAPTURED_DRAW_CAST_FIELD not in effect or DRAW_CAPTURE_FINISHED not in effect:
        return effect
    if effect[DRAW_CAPTURE_FINISHED] is not True:
        raise ValueError("The drawn-card offer must follow the completed draw")
    instruction = {key: value for key, value in effect.items() if key != DRAW_CAPTURE_FINISHED}
    count = captured_draw_cast_instruction(instruction, actor=item.controller)
    hand = _capture(item, count)
    if hand.revealed:
        raise ValueError("A captured draw has already been revealed")
    return {**instruction, "_captured": hand.to_dict()}


def commit_captured_draw_reveal(host, intent):
    item = _live_item(host, intent.actor, intent.stack_ref)
    hand = _capture(item, intent.hand.count)
    if hand != intent.hand:
        raise GameRuleError("The reveal changed its original draw results")
    cards = [card for identity in hand.cards
             if (card := _current_hand_card(host.state, intent.actor, identity)) is not None]
    if intent.keep_revealed:
        item.context[CAPTURED_HAND_CONTEXT] = replace(hand, revealed=True).to_dict()
    else:
        item.context.pop(CAPTURED_HAND_CONTEXT, None)
    if not cards:
        return
    cards.sort(key=lambda card: (card.printed_name.casefold(), card.ref))
    for card in cards:
        card.known_to = sorted(set(card.known_to).union(host.seats))
    host._log(intent.actor, "hand.reveal.captured_draws", f"{intent.actor} revealed cards drawn during resolution.",
        {"player": intent.actor, "source": item.ref, "objects": [card.ref for card in cards],
         "cards": [card.printed_name for card in cards]},
        importance=2, changed_objects=[card.object_id for card in cards], changed_players=[intent.actor])


def _hand_cast_pairs(host, actor, card):
    for method, face in offered_cast_methods(host, card):
        if method not in (None, "fuse"):
            continue
        result = build_cast_offer(host, actor, card, cast_method=method, face_name=face,
                                  resolving_payment="free", resolving_origin="hand")
        if result.offer is None:
            continue
        offer = result.offer.to_dict(include_fingerprint=False)
        for price in offer["cost_options"]:
            canonical = {**price, "face": offer.get("face"), "cast_method": method}
            fingerprint = discover_options_fingerprint((canonical,))
            public = {**canonical, "id": f"drawn-card:{card.ref}:{fingerprint}", "card": card.ref,
                      "label": f"{card.printed_name} — {price.get('label', offer['label'])}"}
            yield public, canonical, card


def _option_pairs(host, actor, hand):
    return tuple(pair for identity in hand.cards
                 if (card := _current_hand_card(host.state, actor, identity)) is not None
                 for pair in _hand_cast_pairs(host, actor, card))


def captured_draw_cast_options(host, *, item, actor, effect):
    if item is None:
        raise GameRuleError("A drawn-card offer requires its resolving stack item")
    _live_item(host, actor, item.ref)
    offer = {key: value for key, value in effect.items() if key != "_options_fingerprint"}
    hand = captured_draw_cast_offer(offer, actor=actor)
    current = _capture(item, hand.count)
    if replace(current, revealed=False) != hand:
        raise GameRuleError("The drawn-card offer changed its original results")
    return tuple(public for public, _, _ in _option_pairs(host, actor, hand))


def execute_captured_draw_cast(host, intent):
    item = _live_item(host, intent.actor, intent.stack_ref)
    hand = _capture(item, intent.hand.count)
    if hand != replace(intent.hand, revealed=True):
        raise GameRuleError("The selected cast lost its revealed draw result")
    pairs = _option_pairs(host, intent.actor, hand)
    if discover_options_fingerprint(tuple(public for public, _, _ in pairs)) != intent.options_fingerprint:
        raise GameRuleError("The selected drawn-card casting options changed")
    if intent.response is not None:
        _commit_selected_cast(host, item, intent, pairs)
    _live_item(host, intent.actor, intent.stack_ref).context.pop(CAPTURED_HAND_CONTEXT)


def _commit_selected_cast(host, item, intent, pairs):
    response = thaw_value(intent.response)
    option_id = response.get("cost_option")
    matches = [(option, card) for public, option, card in pairs
               if option_id in (None, public["id"]) and response.get("card", card.ref) == card.ref
               and response.get("face") in (None, option["face"])
               and response.get("cast_method") in (None, option["cast_method"])]
    if len(matches) != 1 or response.get("from", "hand") != "hand":
        raise GameRuleError("Select one current drawn-card casting option")
    option, card = matches[0]
    original = card.logical_object_id
    response.update(card=card.ref, cost_option=option["id"], auto_pay=True)
    response["from"] = "hand"
    for field in ("face", "cast_method"):
        if option[field] is not None:
            response[field] = option[field]
    commit_resolving_cast(host, actor=intent.actor, response=response, origin="hand",
                          payment_id=f"{item.ref}:captured-draw:{original}",
                          replacement_selections=intent.replacement_selections)
    current = host.state.cards[card.object_id]
    if (current.zone != "stack" or current.controller != intent.actor or current.logical_object_id == original
            or not any(row.kind == "spell" and row.card_object_id == current.object_id for row in host.state.stack)):
        raise StateInvariantError("The selected drawn card created no spell")
