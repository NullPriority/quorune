"""Designation ownership follows typed subscriptions rather than coverage labels."""
from ..designation_event_model import DESIGNATION_EVENTS, DESIGNATION_EVENT_CAPABILITY
from .event_subscriptions import FixedEventSubscriptionSet


def designation_event_runtime_dependencies(program):
    subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
    events = [program.event, *(child.event for child in subscriptions.subscriptions)] if subscriptions else [program.event]
    owners = {DESIGNATION_EVENTS[event.removesuffix('.self')] for event in events if event.removesuffix('.self') in DESIGNATION_EVENTS}
    return tuple(sorted({DESIGNATION_EVENT_CAPABILITY, *owners})) if owners else ()
