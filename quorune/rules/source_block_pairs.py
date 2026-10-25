from __future__ import annotations

"""Create and query a source-specific block relation through the canonical journal."""

from typing import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import commit_continuous_effect, ResolutionEffectSource
from ..errors import GameRuleError
from ..source_block_pair_model import (
    ResolvedSourceBlockPair, SourceBlockPairSpec, SOURCE_BLOCK_PAIR_CAPABILITY, SOURCE_BLOCK_PAIR_KIND,
)
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed


def source_block_pair_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if (set(effect) != {'op', 'card', 'restriction'} or effect.get('op') != 'grant_declaration_restriction_until_end_of_turn'
            or effect.get('card') != '$target.0'):
        return ()
    try:SourceBlockPairSpec.from_dict(effect.get('restriction'))
    except (ValueError, TypeError):return ()
    schema = dict(target_schema or {})
    if not direct_permanent_target_schema_is_closed(schema) or schema.get('count') != 1 or schema.get('types_any') != ['creature']:
        return ()
    if not {'cr-115-targets', 'cr-509-declare-blockers-step', 'cr-611-continuous-effects'} <= set(mechanic_ids):
        return ()
    return (SOURCE_BLOCK_PAIR_CAPABILITY, 'combat.declaration.typed_components', 'target.revalidate_resolution')


def source_block_pair_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            spec = value.get('restriction')
            if (value.get('op') == 'grant_declaration_restriction_until_end_of_turn' and isinstance(spec, Mapping)
                    and spec.get('kind') == SOURCE_BLOCK_PAIR_KIND):
                if set(value) != {'op', 'card', 'restriction'} or value.get('card') != '$target.0':
                    raise ValueError('Source-specific blocking requires one canonical direct target')
                SourceBlockPairSpec.from_dict(spec)
                required.add(SOURCE_BLOCK_PAIR_CAPABILITY)
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit((program.effects, program.handlers))
    return tuple(sorted(required))


def apply_source_block_pair(host, effect, *, actor, reason):
    try:
        SourceBlockPairSpec.from_dict(effect.get('restriction'))
        if set(effect)-{'op', 'card', 'restriction', 'reason', '_runtime_source'} or effect.get('op') != 'grant_declaration_restriction_until_end_of_turn':
            raise ValueError('Source-specific blocking effect fields are closed')
        source = ResolutionEffectSource.from_effect(effect)
        if source.object_id is None or source.logical_object_id is None:
            raise ValueError('Source-specific blocking requires sealed source incarnation')
        attacker = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if effect.get('card') is None:
        return ()
    blocker = host._resolve_object(actor, effect['card'], zones={'battlefield'})
    if blocker.phased_out:
        raise GameRuleError('Source-specific blocking target is phased out')
    rule = ResolvedSourceBlockPair(effect_id=host._next_ref('BP'), source_id=source.object_id,
        timestamp=host._next_zone_timestamp(), attacker=attacker,
        blocker=ContinuousObjectIdentity(blocker.object_id, blocker.logical_object_id))
    commit_continuous_effect(host.state, rule)
    host._log(actor, 'combat.source_block_pair', f'{reason}: source-specific blocking restriction.',
        {'blocker': blocker.ref, 'attacker': source.card_ref, 'continuous_effect': rule.effect_id},
        importance=1, changed_objects=[blocker.object_id])
    return blocker.ref


def pair_identity_matches(identity, card):
    return identity == ContinuousObjectIdentity(card.object_id, card.logical_object_id)


def source_block_pair_prohibits(state, attacker, blocker):
    if any(card.zone != 'battlefield' or card.phased_out for card in (attacker, blocker)):
        return False
    return any(isinstance(rule, ResolvedSourceBlockPair) and pair_identity_matches(rule.attacker, attacker)
        and pair_identity_matches(rule.blocker, blocker) for rule in state.continuous_effects or ())
