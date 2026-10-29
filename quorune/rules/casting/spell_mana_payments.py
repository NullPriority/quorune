from __future__ import annotations

"""Seal paid spell mana and capture its triggers before later casting costs."""

from ...expend_model import EXPEND_CAPABILITY, SPELL_MANA_PAYMENT_EVENT, SpellManaPaymentEvent
from ...turn_history import current_turn_history_events
from ...rules.event_subscriptions import FixedEventSubscriptionSet


def current_spell_mana_paid(history, *, turn_sequence, player):
    if history is None or history.spell_mana_history_version != 1:
        return None
    return sum(event.amount for event in current_turn_history_events(history,
        turn_sequence=turn_sequence,kind='spell_mana_spent') if event.actor == player)


def capture_spell_mana_payment(host, *, player, spent):
    history=host.state.turn_history
    before=current_spell_mana_paid(history,turn_sequence=host.state.turn_sequence,player=player)
    if before is None:
        return []
    if any(type(amount) is not int or amount < 0 for amount in spent.values()):
        raise ValueError('Spell payment must contain actual nonnegative mana counts')
    total=sum(spent.values())
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
