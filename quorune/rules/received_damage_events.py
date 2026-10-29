"""Receiving-event ownership follows subscriptions, independently of coverage."""
from ..received_damage_model import (
    RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_CAPABILITY,
)
from .event_subscriptions import FixedEventSubscriptionSet


def received_damage_runtime_dependencies(program):
    subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
    events = [program.event, *(child.event for child in subscriptions.subscriptions)] if subscriptions else [program.event]
    if any(event.removesuffix('.self') in {RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT} for event in events):
        return (RECEIVED_DAMAGE_CAPABILITY, 'trigger.event.normalized_damage')
    return ()
