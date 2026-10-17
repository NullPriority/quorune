from __future__ import annotations

"""Closed temporary blocking permission through the existing duration journal."""

from typing import Mapping

from ..block_capacity_model import BlockCapacitySpec, BLOCK_CAPACITY_CAPABILITY
from ..block_capacity_rule_model import (
    ResolvedBlockCapacityRule, TEMPORARY_BLOCK_CAPACITY, TEMPORARY_BLOCK_CAPACITY_MECHANIC,
)
from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effects import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ResolutionContinuousComponent, commit_continuous_effect,
    create_resolution_continuous_effect_components, resolution_effect_source,
)
from ..errors import GameRuleError


def decode_temporary_block_capacity(effect, *, allow_resolved_absence=False):
    fields = {'op', 'schema_version', 'card', 'capacity', 'power', 'toughness'}
    if not isinstance(effect, Mapping) or set(effect) - {'_runtime_source'} != fields:
        raise ValueError('Temporary blocking capacity has a closed schema')
    if (effect['op'] != 'apply_source_characteristics_until_end_of_turn'
            or type(effect['schema_version']) is not int or effect['schema_version'] != 10):
        raise ValueError('Temporary blocking capacity requires version 10')
    if (not (allow_resolved_absence and effect['card'] is None)
            and (type(effect['card']) is not str or not effect['card'])):
        raise ValueError('Temporary blocking capacity requires one object reference')
    if type(effect['power']) is not int or type(effect['toughness']) is not int:
        raise ValueError('Temporary blocking modifiers require fixed integers')
    return BlockCapacitySpec.from_dict(effect['capacity'])


def temporary_block_capacity_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or TEMPORARY_BLOCK_CAPACITY_MECHANIC not in mechanic_ids:
        return ()
    effect = effects[0]
    try:
        decode_temporary_block_capacity(effect)
    except (KeyError, TypeError, ValueError):
        return ()
    dependencies = {TEMPORARY_BLOCK_CAPACITY, BLOCK_CAPACITY_CAPABILITY,
        'continuous.resolution.fixed_characteristics_until_end_of_turn'}
    if effect['card'] == '$source.zone_object':
        if target_schema is not None:
            return ()
    elif effect['card'] == '$target.0':
        from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed
        from .node_capability_shapes import direct_target_predicate_capabilities
        if (not direct_permanent_target_schema_is_closed(target_schema)
                or target_schema.get('types_any', target_schema.get('types_all')) != ['creature']):
            return ()
        dependencies.update(direct_target_predicate_capabilities(target_schema))
        dependencies.add('target.revalidate_resolution')
    else:
        return ()
    return tuple(sorted(dependencies))


def apply_temporary_block_capacity(host, effect, *, actor, reason):
    try:
        capacity = decode_temporary_block_capacity(effect, allow_resolved_absence=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if effect['card'] is None:
        return ()
    try:
        card = host._resolve_object(actor, effect['card'], zones={'battlefield'})
    except GameRuleError:
        return ()
    source = resolution_effect_source(host, effect, fallback_card=card)
    rule = ResolvedBlockCapacityRule(effect_id=host._next_ref('BC'),
        source_id=source.object_id or source.stack_ref, timestamp=host._next_zone_timestamp(),
        subject=ContinuousObjectIdentity(card.object_id, card.logical_object_id), capacity=capacity)
    if effect['power'] or effect['toughness']:
        component = ResolutionContinuousComponent(layer=Layer.POWER_TOUGHNESS, sublayer='7c',
            operations=(ContinuousOperation('modify_power_toughness', [effect['power'], effect['toughness']]),))
        create_resolution_continuous_effect_components(host, source=source, targets=(card,), components=(component,))
    commit_continuous_effect(host.state, rule)
    host._log(actor, 'combat.block_capacity', f'{reason}: temporary blocking capacity changes.',
        {'object': card.ref, 'capacity': capacity.to_dict()}, importance=1, changed_objects=[card.object_id])
    return (card.ref,)
