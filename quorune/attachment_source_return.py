from __future__ import annotations

"""Follow an enchanted permanent's departing Aura through CR 400.7f."""

from collections.abc import Mapping

from .attachment_source_return_model import (
    AttachmentDepartureSource, OPERATION, SOURCE_FIELD, SOURCE_REFERENCE,
    validate_attachment_source_return, is_attachment_source_return,
)
from .errors import GameRuleError


def attachment_source_return_context(source, program):
    if not any(is_attachment_source_return(effect) for effect in program.effects):
        return {}
    if source.zone != 'battlefield' or source.phased_out or source.object_kind not in {'card', 'token'}:
        raise ValueError('An attachment return requires its battlefield Aura occurrence')
    link = AttachmentDepartureSource(source.object_id, source.ref, source.logical_object_id,
                                    source.zone_change_counter, source.owner)
    return {SOURCE_FIELD: link.to_dict()}


def bind_attachment_source_return(effect, item):
    if effect.get('source') == SOURCE_REFERENCE:
        validate_attachment_source_return(effect, compiled=True)
        result = {**effect, 'source': item.context.get(SOURCE_FIELD)}
    else:
        result = dict(effect)
    validate_attachment_source_return(result)
    link = AttachmentDepartureSource.from_dict(result['source'])
    if (link.object_id != item.source_object_id
            or link.logical_object_id != item.context.get('source_logical_object_id')):
        raise ValueError('Attachment return changed its triggering source incarnation')
    return result


def _schedule_attachment_return(host, effect, link, *, actor):
    from .delayed_object_model import OBJECT_TIMINGS
    from .trigger_processing import schedule_delayed_trigger
    future = {k: v for k, v in effect.items() if k not in {'reason', '_runtime_source'}}
    future['phase'] = 'return'
    return schedule_delayed_trigger(host, controller=actor, label='Return departed Aura',
        event_kind='step.begin', condition=OBJECT_TIMINGS['next_end_step'],
        stack_template={'label': 'Return departed Aura', 'context': {'dynamic_effects': [future]}},
        source_object_id=link.object_id, source_logical_object_id=link.logical_object_id,
        referred_object_ids=(link.object_id,), once=True).ref


def apply_attachment_source_return(host, effect, *, actor, operation, reason):
    try:
        spec = validate_attachment_source_return(effect)
        link = AttachmentDepartureSource.from_dict(effect['source'])
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if operation != OPERATION or actor not in host.active_seats:
        raise GameRuleError('Attachment return requires its resolving controller')
    if spec.timing == 'next_end_step' and effect['phase'] == 'start':
        return _schedule_attachment_return(host, effect, link, actor=actor)
    card = host.state.cards.get(link.object_id)
    if (not link.matches_first_graveyard(card)
            or not spec.allows_graveyard(graveyard_owner=card.owner, trigger_controller=actor)):
        return None
    face = None
    if spec.transformed:
        from .linked_exile_return import transformed_return_face
        face = transformed_return_face(host.card_record(card))
        if face is None:
            return None
    moved = host.move_card(card.object_id, 'battlefield', controller=actor, enter_face=face,
        reason=reason, semantic_events=True,
        aura_target_ref=effect.get('_aura_target_ref'),
        replacement_selections=tuple(effect.get('_replacement_selections', ())))
    return moved.ref if moved is not None and moved.zone == 'battlefield' else None
