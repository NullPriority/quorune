from __future__ import annotations

"""Link a committed draw to its original trigger and derive reveal lifetime."""

from ..card_programs.admission import program_has_complete_card_program_admission
from ..model import StackItem
from ..semantic_runtime.intrinsic_draw_reveals import MiracleCastTriggerHandler
from .miracle_model import MiracleDrawOpportunity, MiracleModelError, MiracleRevealedHand, MIRACLE_TRIGGER_EVENT


MIRACLE_HAND_CONTEXT_FIELD = "miracle_revealed_hand"


def miracle_trigger_context(*, source, program, event, context, trigger_ref):
    if "miracle_linked_cast_window" not in program.coverage:
        return {}
    if (event != MIRACLE_TRIGGER_EVENT.removesuffix('.self')
            or program.event != MIRACLE_TRIGGER_EVENT
            or program.oracle_id != source.oracle_id
            or not program_has_complete_card_program_admission(program)
            or len(program.handlers) != 1):
        raise MiracleModelError('Miracle trigger lost its complete source declaration')
    cost = MiracleCastTriggerHandler().validate(program.handlers[0])
    opportunity = MiracleDrawOpportunity.from_dict(context.get('miracle_opportunity'))
    if opportunity.cost != cost or opportunity.drawer != source.owner:
        raise MiracleModelError('Miracle trigger disagrees with the revealed draw')
    hand = opportunity.bind_revealed_hand(source, reveal_trigger_ref=trigger_ref)
    return {MIRACLE_HAND_CONTEXT_FIELD: hand.to_dict()}


def revealed_hand_for_trigger(item) -> MiracleRevealedHand | None:
    context = item.context if isinstance(item, StackItem) else item.event_facts
    if MIRACLE_HAND_CONTEXT_FIELD not in context:
        return None
    return MiracleRevealedHand.from_dict(context[MIRACLE_HAND_CONTEXT_FIELD])


def miracle_reveal_active(state, card) -> bool:
    """Only the original pending/live trigger sustains this source's reveal.

    Copies retain the linked cast subject, but do not extend the original
    disclosure. Other reveals and historical card knowledge are untouched.
    """
    if card.zone != 'hand':
        return False
    candidates = (*state.stack, *(item for batch in state.pending_trigger_batches for item in batch.items))
    for item in candidates:
        hand = revealed_hand_for_trigger(item)
        if hand is None or item.ref != hand.reveal_trigger_ref:
            continue
        try:
            hand.require_current_hand(card)
        except MiracleModelError:
            continue
        return True
    return False
