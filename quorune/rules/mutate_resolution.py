"""Resolve an authorized Mutate alternative through the physical merge owner."""

from ..errors import GameRuleError
from ..mutate_model import MutateSpec,MUTATE_PAYMENT_FIELD,MUTATE_EVENT
from ..semantic_runtime.mutate_intent import SelectMutateMergeIntent
from ..replacement.immutable import FrozenMap
from ..target_source import stack_targeting_source
from ..targets import target_plan
from ..zone_transitions import ZoneTransitionOwner
from .composition_merge import prepare_composition_merge


def mutating_spell_spec(item):
    if MUTATE_PAYMENT_FIELD not in item.context:return None
    try:spec=MutateSpec.from_dict(item.context[MUTATE_PAYMENT_FIELD])
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    if (item.kind not in {'spell','spell_copy'}
            or len(item.targets)!=1
            or item.context.get('cost_option') not in {o['id'] for o in spec.cast_cost_options()}
            or FrozenMap(item.context.get('target_schema_override') or {})!=FrozenMap(spec.target_schema())
            or item.context.get('cast_option_effects')!=[{'op':'mutate_merge','target':'$target.0'}]):
        raise GameRuleError('Mutate resolution requires its announced alternative cost and target contract')
    return spec


def commit_mutate_merge(host,intent):
    if not isinstance(intent,SelectMutateMergeIntent):raise GameRuleError('Mutate completion requires its typed selection')
    item=host.state.stack[-1] if host.state.stack else None
    if (item is None or item.ref!=intent.stack_ref or item.controller!=intent.actor
            or item.context.get('currently_resolving') is not True
            or item.context.get('targets_revalidated') is not True):
        raise GameRuleError('Mutate completion lost its current resolving spell')
    source=host.state.cards.get(item.card_object_id or '')
    root=host.state.cards.get(intent.target.object_id)
    if (source is None or root is None or source.object_id!=intent.source.object_id
            or source.logical_object_id!=intent.source.logical_object_id
            or root.logical_object_id!=intent.target.logical_object_id or source.owner!=root.owner):
        raise GameRuleError('Mutate completion has stale source, target or spell ownership')
    spec=mutating_spell_spec(item)
    if spec is None or item.targets!=[root.ref]:
        raise GameRuleError('Mutate completion changed its announced target')
    plan=target_plan(spec.target_schema())
    candidates=host._target_candidate_map(item.controller,plan,source_ref=host._stack_source_ref(item),
        acting_source=stack_targeting_source(host,item))
    if root.ref not in candidates[plan.groups[0].group_id]:
        raise GameRuleError('Mutate completion target is no longer legal')
    with host.transaction():
        prepared=prepare_composition_merge(host,target=intent.target,source=intent.source,
            stack_ref=item.ref,position=intent.position)
        storage=ZoneTransitionOwner(host).commit_composition_merge(prepared)
        host._dispatch_semantic_event(MUTATE_EVENT,{'card':root.ref,'object_ref':root.ref,'object_id':root.object_id,
            'logical_object_id':root.logical_object_id,'controller':root.controller,
            'mutation_count':storage.topology.mutation_count})
        host._log(item.controller,'stack.resolve',f'Resolved {item.ref} {item.label}.',
            {'stack':item.ref,'merged_permanent':root.ref},importance=2)
        if not host._stabilize():host._grant_priority(host.state.active_player)
        return storage
