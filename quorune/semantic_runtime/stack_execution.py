from __future__ import annotations

"""Canonical delegation of typed stack changes and trigger creation."""

from .intents import CounterStackIntent, CopyStackItemIntent, RetargetStackItemIntent
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from ..reflexive_trigger_model import QueueReflexiveTriggerIntent, QueueRevealUntilReflexiveIntent


STACK_INTENT_TYPES = (CounterStackIntent, CopyStackItemIntent, RetargetStackItemIntent,
                      QueueReflexiveTriggerIntent, QueueRevealUntilReflexiveIntent)


def execute_stack_copy(host, intent: CopyStackItemIntent) -> str:
    from ..rules.event_spell_copy import resolve_event_spell_copy_source
    from ..rules.exile_spell_copy_grant import resolving_exile_spell_copy_grant

    bound = resolve_event_spell_copy_source(
        host, {"stack": intent.target_stack_ref, "copy_source": intent.copy_source},
    ) if intent.copy_source is not None else None
    target = bound.item() if bound is not None else next(
        (item for item in host.state.stack if item.ref == intent.target_stack_ref), None,
    )
    if target is None:
        raise GameRuleError("The stack object selected for copying no longer exists")
    characteristics = thaw_value(bound.characteristics) if bound is not None else None
    if intent.permanent_copy is not None and intent.permanent_copy.kind != "exile_cast_grant":
        from ..rules.stack_spell_copy_shapes import apply_permanent_spell_copy_exception

        if characteristics is None:
            original = host.state.cards.get(target.card_object_id or "")
            if original is None:
                raise GameRuleError("Permanent spell copy lost its original card or snapshot")
            characteristics = host._spell_copy_characteristics(original)
        try:
            characteristics = apply_permanent_spell_copy_exception(intent.permanent_copy, characteristics)
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
    with resolving_exile_spell_copy_grant(host, intent, bound):
        copied = host._copy_stack_item(
            controller=intent.controller,
            target=target,
            targets=list(intent.targets),
            target_groups=thaw_value(intent.target_groups),
            reason=intent.reason,
            target_snapshots=thaw_value(intent.target_snapshots) if intent.target_snapshots is not None else None,
            copiable_characteristics=characteristics,
        )
    return copied.ref


def execute_stack_intent(sink, intent):
    if isinstance(intent, QueueRevealUntilReflexiveIntent):
        from ..rules.reveal_until_reflexive import enqueue_reveal_until_reflexive
        return intent.controller, enqueue_reveal_until_reflexive(sink, intent)
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
