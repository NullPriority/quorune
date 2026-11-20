from __future__ import annotations

"""Read-only dealer preparation; canonical trigger/zone/damage owners keep mutation."""

from collections.abc import Mapping
from dataclasses import replace
from ..event_damage_source_model import (EventDamageSourceReference, EventDamageSourceInformation,
    EVENT_DAMAGE_SOURCE_KIND, EVENT_DAMAGE_SOURCE_CONTEXT, EVENT_DAMAGE_SOURCE_CAPABILITY)
from ..event_counter_reference import event_object_identity

_OPERATIONS = frozenset({'damage', 'damage_each_opponent', 'damage_fixed_set'})


def _permanent_received_condition(condition):
    if not isinstance(condition, Mapping):
        return False
    if condition.get('field') == 'target_kind' and condition.get('op') == 'eq' and condition.get('value') == 'permanent':
        return True
    return any(_permanent_received_condition(child) for child in condition.get('all', ()))


def event_damage_leaves(value):
    if isinstance(value, Mapping):
        source = value.get('source')
        if isinstance(source, Mapping) and (value.get('op') in _OPERATIONS or source.get('kind') == EVENT_DAMAGE_SOURCE_KIND):
            if value.get('op') not in _OPERATIONS:
                raise ValueError('Event damage source belongs to a damage instruction')
            EventDamageSourceReference.from_dict(source)
            yield value
        for child in value.values():
            yield from event_damage_leaves(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from event_damage_leaves(child)


def event_damage_source_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or not isinstance(effects[0], Mapping) or not isinstance(effects[0].get('source'), Mapping):
        return ()
    effect = effects[0]
    try:
        reference = EventDamageSourceReference.from_dict(effect['source'])
        if effect.get('op') not in _OPERATIONS:
            return ()
        amount = effect.get('amount')
        if isinstance(amount, Mapping):
            from ..scalar_effect_amount_model import ScalarEffectAmountSpec, ScalarAmountOrigin
            scalar = ScalarEffectAmountSpec.from_dict(amount)
            if scalar.origin is not ScalarAmountOrigin.EVENT_AMOUNT or reference.event not in {'damage.received', 'combat.damage.received'}:
                return ()
            amount = 1
        if type(amount) is not int or amount <= 0:
            return ()
    except (TypeError, ValueError):
        return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities
    projected = {**effect, 'source': '$source', 'amount': amount}
    base = fixed_damage_node_capabilities(effects=(projected,), target_schema=target_schema, mechanic_ids=mechanic_ids)
    return tuple(sorted({*base, EVENT_DAMAGE_SOURCE_CAPABILITY})) if base else ()


def event_damage_source_runtime_dependencies(program):
    leaves = tuple(event_damage_leaves(program.effects))
    if not leaves:
        return ()
    if len(leaves) != 1 or program.active_zone != 'battlefield':
        raise ValueError('Event dealer requires one battlefield-triggered damage instruction')
    effect = leaves[0];reference = EventDamageSourceReference.from_dict(effect['source'])
    if program.event != reference.event or 'event_subscriptions' in (program.event_condition or {}):
        raise ValueError('Event dealer and its unique normalized subscription must agree')
    if reference.event in {'damage.received', 'combat.damage.received'} and not _permanent_received_condition(program.event_condition):
        raise ValueError('A receiving dealer requires the permanent-recipient subscription')
    required = event_damage_source_node_capabilities(effects=(effect,), target_schema=program.target_schema,
        mechanic_ids=('cr-120-damage', *(('cr-115-targets',) if program.target_schema is not None else ())))
    if not required:
        raise ValueError('Event dealer damage result is outside its closed owner')
    return required


def _information(context, reference):
    info = EventDamageSourceInformation.from_dict(context.get(EVENT_DAMAGE_SOURCE_CONTEXT))
    if context.get('event') != reference.event or info.event != reference.event:
        raise ValueError('Event damage source lost its proved occurrence')
    if (info.received_amount is not None and (context.get('event_id') != info.event_id
            or type(context.get('amount')) is not int or context['amount'] != info.received_amount)):
        raise ValueError('Event damage source lost its sealed received amount or transaction')
    identity = event_object_identity(reference.event, context)
    if (info.source_snapshot.ref != identity['reference']
            or info.source_snapshot.logical_object_id != identity['logical_object_id']):
        raise ValueError('Event damage source information names another incarnation')
    return info


def event_damage_source_context(host, program, event, context):
    leaves = tuple(event_damage_leaves(program.effects))
    if not leaves:
        return {}
    event_damage_source_runtime_dependencies(program)
    reference = EventDamageSourceReference.from_dict(leaves[0]['source'])
    if event != reference.event:
        raise ValueError('Event damage source was queued by another event')
    identity = event_object_identity(event, context)
    object_id = identity['logical_object_id'].rpartition('@')[0]
    card = host.state.cards.get(object_id)
    if (card is None or card.ref != identity['reference'] or card.logical_object_id != identity['logical_object_id']
            or card.zone != 'battlefield' or card.phased_out):
        raise ValueError('Event damage dealer is unavailable at its occurrence')
    from .stack_damage_sources import _public_source_snapshot
    received = event in {'damage.received', 'combat.damage.received'}
    info = EventDamageSourceInformation(event, _public_source_snapshot(host, card),
        event_id=context.get('event_id') if received else None,
        received_amount=context.get('amount') if received else None)
    return {EVENT_DAMAGE_SOURCE_CONTEXT: info.to_dict()}


def bind_event_damage_source(host, effect, item):
    if not isinstance(effect.get('source'), Mapping):
        return {}
    reference = EventDamageSourceReference.from_dict(effect['source'])
    info = _information(item.context, reference);snapshot = info.source_snapshot
    card = host.state.cards.get(snapshot.object_id)
    if (card is not None and card.logical_object_id == snapshot.logical_object_id
            and card.zone == 'battlefield' and not card.phased_out):
        from .stack_damage_sources import _public_source_snapshot
        snapshot = _public_source_snapshot(host, card)
    return {'source': snapshot.ref, 'source_snapshot': snapshot.to_dict()}


def event_damage_departure_updates(host, frames, departing):
    from .stack_damage_sources import _public_source_snapshot
    updates = {}
    for item in frames:
        context = item.get('context', {})
        if EVENT_DAMAGE_SOURCE_CONTEXT not in context:
            continue
        raw = context[EVENT_DAMAGE_SOURCE_CONTEXT];info = EventDamageSourceInformation.from_dict(raw)
        _information(context, EventDamageSourceReference(info.event))
        snapshot = info.source_snapshot
        card = departing.get((snapshot.object_id, snapshot.logical_object_id))
        if card is not None:
            updates[item['ref']] = replace(info, source_snapshot=_public_source_snapshot(host, card)).to_dict()
    return updates
