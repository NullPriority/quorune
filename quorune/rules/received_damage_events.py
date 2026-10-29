"""Receiving-event ownership follows subscriptions, independently of coverage."""
from collections.abc import Mapping
from ..received_damage_model import (
    RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_CAPABILITY,
)
from .event_subscriptions import FixedEventSubscriptionSet


def received_damage_runtime_dependencies(program):
    subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
    events = [program.event, *(child.event for child in subscriptions.subscriptions)] if subscriptions else [program.event]
    if any(event.removesuffix('.self') in {RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT} for event in events):
        def attachment(value):
            if isinstance(value,Mapping):return value.get('field')=='source_attachment_target_ref' or any(attachment(child) for child in value.values())
            if isinstance(value,(list,tuple)):return any(attachment(child) for child in value)
            return False
        return (RECEIVED_DAMAGE_CAPABILITY, 'trigger.event.normalized_damage',*(('attachment.reference.current_or_lki',) if attachment(program.event_condition) else ()))
    return ()
