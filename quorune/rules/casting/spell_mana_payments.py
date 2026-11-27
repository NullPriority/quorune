from __future__ import annotations

"""Seal paid spell mana and capture its triggers before later casting costs."""

from ...expend_model import EXPEND_CAPABILITY, SPELL_MANA_PAYMENT_EVENT, SpellManaPaymentEvent
from ...mana_restrictions import bind_exiled_spell_mana_spending
from ...turn_history import current_turn_history_events
from ..event_subscriptions import FixedEventSubscriptionSet
from ..mana_pool_payment import ManaPoolAllocation


def cast_mana_spend_context(host, seat, card, type_line):
    """Bind spend permission while the offered or announced cast is still in its origin zone."""
    context = host._spell_mana_spend_context(type_line)
    if card.zone == "exile":
        from ...private_opponent_library_cast_model import has_private_opponent_library_mana_waiver

        if has_private_opponent_library_mana_waiver(host, actor=seat, card=card):
            return bind_exiled_spell_mana_spending(context, payer=seat)
    return context


def _chosen_monocolored_mana_relevant(host, seat):
    from ...mana_provenance import mana_provenance_lots
    from ...mana_spend_rules import CHOSEN_MONOCOLORED_RESTRICTION

    player = host.state.players[seat]
    if any(lot.restriction and lot.restriction.startswith(CHOSEN_MONOCOLORED_RESTRICTION + "|")
           for lot in mana_provenance_lots(player)):
        return True
    return any(ability.mana_spend_restriction == CHOSEN_MONOCOLORED_RESTRICTION
        for oid in player.zones["battlefield"] for card in (host.state.cards[oid],)
        if not card.phased_out and card.controller == seat
        for ability in host._activated_abilities(card))


def spell_payment_colors(host, seat, card, spend_context, *, face_down=False):
    if not _chosen_monocolored_mana_relevant(host, seat):
        return spend_context
    from dataclasses import replace
    from ...mana_restrictions import with_spell_spend_colors

    view = card if card.zone == "stack" else replace(card, zone="stack", controller=seat)
    colors = () if face_down else tuple(host._effective_card_data(view).get("colors") or ())
    return with_spell_spend_colors(spend_context, colors)


def cast_cost_option_mana_spend_context(host, seat, card, selected_type_line, *,
                                       option=None, suppress_opponent_exile_mana=False, face_down=False):
    """Use the selected face's type when estimating a spell's mana payment."""
    type_line = selected_type_line
    if type_line is None:
        type_line = host._effective_card_data(card).get("type_line") or ""
    from ...opponent_library_exile import LEGACY_FREE_EXILE_CAST_FIELD, opponent_library_exile_spend_context

    context = spell_payment_colors(host, seat, card,
        cast_mana_spend_context(host, seat, card, str(type_line)), face_down=face_down)
    if suppress_opponent_exile_mana or (option or {}).get(LEGACY_FREE_EXILE_CAST_FIELD):
        return context
    return opponent_library_exile_spend_context(host.state, seat, card, context)


def current_spell_mana_paid(history, *, turn_sequence, player):
    if history is None or history.spell_mana_history_version != 1:
        return None
    return sum(event.amount for event in current_turn_history_events(history,
        turn_sequence=turn_sequence,kind='spell_mana_spent') if event.actor == player)


def capture_spell_mana_payment(host, *, player, paid: ManaPoolAllocation):
    history=host.state.turn_history
    before=current_spell_mana_paid(history,turn_sequence=host.state.turn_sequence,player=player)
    if before is None:
        return []
    total = sum(amount for _, amount in paid.units)
    if total == 0:
        return []
    event=SpellManaPaymentEvent(player,before,before+total)
    host._record_turn_history('spell_mana_spent',actor=player,amount=total)
    triggers=[]
    host._dispatch_semantic_event(SPELL_MANA_PAYMENT_EVENT,event.to_context(),trigger_batch=triggers)
    return triggers


def expend_runtime_dependencies(program):
    subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
    events=(child.event for child in subscriptions.subscriptions) if subscriptions else (program.event,)
    return (EXPEND_CAPABILITY,) if SPELL_MANA_PAYMENT_EVENT in events else ()
