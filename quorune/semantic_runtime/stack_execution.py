from __future__ import annotations

"""Canonical delegation of typed stack changes and trigger creation."""

from .intents import CounterStackIntent, CopyStackItemIntent, RetargetStackItemIntent
from ..reflexive_trigger_model import QueueReflexiveTriggerIntent


STACK_INTENT_TYPES = (CounterStackIntent, CopyStackItemIntent, RetargetStackItemIntent, QueueReflexiveTriggerIntent)


def execute_stack_intent(sink, intent):
    if isinstance(intent, QueueReflexiveTriggerIntent):
        from ..rules.reflexive_triggers import enqueue_reflexive_trigger
        return intent.controller, enqueue_reflexive_trigger(sink, intent)
    if isinstance(intent, CounterStackIntent):
        sink.counter_stack_intent(intent)
        return intent.stack_ref, None
    if isinstance(intent, CopyStackItemIntent):
        return intent.target_stack_ref, sink.copy_stack_item_intent(intent)
    if isinstance(intent, RetargetStackItemIntent):
        return intent.target_stack_ref, sink.retarget_stack_item_intent(intent)
    raise TypeError('Unknown typed stack intent')
