"""Receiving-event ownership follows subscriptions, independently of coverage."""
from collections.abc import Mapping
from ..received_damage_model import (
    RECEIVED_DAMAGE_EVENTS, RECEIVED_DAMAGE_CAPABILITY,
)
from .event_subscriptions import FixedEventSubscriptionSet


def is_creature_receiving_event(event, condition):
    """Recognize a normalized receiving event that names one damaged creature."""
    if event not in RECEIVED_DAMAGE_EVENTS or not isinstance(condition, Mapping):
        return False
    predicates = condition.get('all')
    if set(condition) != {'all'} or not isinstance(predicates, (list, tuple)):
        return False
    return (
        {'field': 'target_kind', 'op': 'eq', 'value': 'permanent'} in predicates
        and any(
            isinstance(value, Mapping) and set(value) == {'field', 'op', 'value'}
            and value['field'] == 'target_types' and value['op'] == 'contains_any'
            and value['value'] in (['creature'], ('creature',))
            for value in predicates
        )
    )


def received_damage_runtime_dependencies(program):
    subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
    events = [program.event, *(child.event for child in subscriptions.subscriptions)] if subscriptions else [program.event]
    if any(event.removesuffix('.self') in RECEIVED_DAMAGE_EVENTS for event in events):
        def attachment(value):
            if isinstance(value,Mapping):return value.get('field')=='source_attachment_target_ref' or any(attachment(child) for child in value.values())
            if isinstance(value,(list,tuple)):return any(attachment(child) for child in value)
            return False
        return (RECEIVED_DAMAGE_CAPABILITY, 'trigger.event.normalized_damage',*(('attachment.reference.current_or_lki',) if attachment(program.event_condition) else ()))
    return ()
