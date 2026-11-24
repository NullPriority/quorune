"""Bind the original source and delegate sacrifice to canonical zone movement."""
from ..attachment_references import AttachmentReferenceKind, SourceAttachmentSnapshot
from ..replacement.immutable import FrozenMap
from ..rules.committed_quantity_followups import CommittedQuantityFollowup
from ..source_sacrifice_model import (
    CAST_PARTICIPANT_FIELD,
    FORMER_ATTACHMENT_SACRIFICE_VERSION, SOURCE_SACRIFICE_FIELD, UNATTACHMENT_SNAPSHOT,
    FixedSourceSacrificeIntent, fixed_source_sacrifice_dependencies,
)
from .context import SemanticNodeError
from .intents import IntentPlan, MoveObjectsSimultaneouslyIntent
from ..zone_trigger_events import ZoneTransitionKind


def lower_fixed_source_sacrifice(effect, context, *, handler_id):
    try:
        if not fixed_source_sacrifice_dependencies(effect, resolved=True):
            raise ValueError('Source sacrifice needs its explicit instruction version')
        if context.actor not in context.query.active_seats:
            raise ValueError('The resolving source-sacrifice controller is unavailable')
        source = context.source
        if source is None or not source.stack_ref:
            raise ValueError('Source sacrifice needs its resolving source frame')
        identity = (source.object_id, source.logical_object_id, source.card_ref)
        if effect[SOURCE_SACRIFICE_FIELD] == FORMER_ATTACHMENT_SACRIFICE_VERSION:
            snapshot = SourceAttachmentSnapshot.from_dict(effect[UNATTACHMENT_SNAPSHOT])
            former = snapshot.attached_object
            if (snapshot.relation is not AttachmentReferenceKind.EQUIPPED or former is None
                    or former.object_id == snapshot.source.object_id):
                raise ValueError('Unattachment sacrifice requires the exact former equipped permanent')
            if ((source.object_id is not None and source.object_id != snapshot.source.object_id)
                    or (source.logical_object_id is not None
                        and source.logical_object_id != snapshot.source.logical_object_id)):
                raise ValueError('Former attachment sacrifice requires its original Equipment frame')
            identity = (former.object_id, former.logical_object_id, former.reference)
        if all(value is None for value in identity):
            return IntentPlan(operation='self_zone_move', handler_id=handler_id,
                              intents=(), result_shape='by_player')
        participant = effect.get(CAST_PARTICIPANT_FIELD)
        if participant is not None:
            context.query.require_known_seat(participant)
        intent = FixedSourceSacrificeIntent(context.actor, source.stack_ref, *identity,
            context.default_reason, tuple(effect.get('_replacement_selections', ())), cast_participant=participant)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return IntentPlan(operation='self_zone_move', handler_id=handler_id, intents=(intent,))


def apply_fixed_source_sacrifice(host, intent):
    if not isinstance(intent, FixedSourceSacrificeIntent):
        raise ValueError('Source sacrifice requires its immutable source intent')
    intent.__post_init__()
    card = host.state.cards.get(intent.object_id)
    if (card is None or card.ref != intent.card_ref
            or card.logical_object_id != intent.logical_object_id
            or card.zone != 'battlefield' or card.phased_out or card.controller != intent.actor):
        return None if intent.cast_participant is None else CommittedQuantityFollowup(())
    moved = host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
        actor=intent.actor, object_refs=(card.ref,), expected_zones=('battlefield',),
        destination='graveyard', reason=intent.reason, transition_kind=ZoneTransitionKind.SACRIFICE,
        controlled_only=True, replacement_selections=intent.replacement_selections))
    if intent.cast_participant is None:
        return moved
    # CR 118.12 checks that the mandatory cost started, including a replaced destination.
    opponents = tuple(seat for seat in host.apnap_order() if seat != intent.cast_participant)
    if not opponents:
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        'op': 'draw_each_player', 'players': opponents, 'count': 3, 'private': True,
    }),))
