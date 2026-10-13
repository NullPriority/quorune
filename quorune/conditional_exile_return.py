from __future__ import annotations

"""Conditional return movement over the canonical simultaneous zone owner.

Captured source departures supply one immediate return set independently of
triggered-ability placement. Return entry choices retain their own continuation
so the already-committed source movement is not repeated.
"""

from typing import Any, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

from .conditional_exile_model import (
    ConditionalExileBinding, conditional_departure_return_objects, conditional_source_remains,
)
from .continuous_effect_model import ContinuousObjectIdentity
from .errors import GameRuleError
from .zone_transitions import ZoneTransitionOwner
from .replacement.ordering import ReplacementChoiceRequired
from .aura import AuraEntryChoiceRequired
_DEFERRED_RETURNS: ContextVar[list[Any] | None] = ContextVar('conditional_cost_returns', default=None)


def conditional_exile_source(effect: Mapping[str,Any]) -> ContinuousObjectIdentity:
    source=effect.get('_runtime_source')
    if not isinstance(source,Mapping):raise GameRuleError('Conditional exile requires authoritative source context')
    try:return ContinuousObjectIdentity(source['object_id'],source['logical_object_id'])
    except (KeyError,ValueError) as exc:raise GameRuleError('Conditional exile source identity is unavailable') from exc


def retain_or_return_conditional_exile(host: Any, item: Any, binding_id: str, objects: Sequence[Any], effect: Mapping[str,Any]):
    from .linked_exile_return_model import ExiledObjectIdentity
    if not objects:return []
    source=conditional_exile_source(effect)
    binding=ConditionalExileBinding(item.ref+':'+binding_id,source,
        tuple(ExiledObjectIdentity.from_dict(value) for value in objects))
    if conditional_source_remains(source,host.state.cards):
        ZoneTransitionOwner(host).remember_conditional_exile(binding)
        return []
    return [card.ref for card in return_after_source_departures(host,(binding,),(source,))]


def conditional_cost_returns_pending() -> bool:
    return bool(_DEFERRED_RETURNS.get())


@contextmanager
def defer_conditional_cost_returns():
    """Keep native cost payment and stack placement in their existing owner."""
    waiting = []
    token = _DEFERRED_RETURNS.set(waiting)
    try:
        yield waiting
    finally:
        _DEFERRED_RETURNS.reset(token)


def finish_conditional_cost_returns(host: Any, waiting: Sequence[Any]) -> bool:
    bindings = tuple(binding for captured, _departures in waiting for binding in captured)
    departures = tuple(dict.fromkeys(source for _captured, departed in waiting for source in departed))
    if not bindings:
        return False
    token = _DEFERRED_RETURNS.set(None)
    try:
        return_after_source_departures(host, bindings, departures)
    except ConditionalReturnChoiceRequired as required:
        issue_conditional_return_choice(host, required)
        return True
    except ConditionalReturnAuraChoiceRequired as required:
        issue_conditional_aura_choice(host,required)
        return True
    finally:
        _DEFERRED_RETURNS.reset(token)
    return False


def commit_activation_with_conditional_returns(host: Any, proposal: Any, response: Mapping[str, Any]) -> None:
    from .rules.activation.commit import _commit_activation_costs_and_stack
    with defer_conditional_cost_returns() as waiting:
        result = _commit_activation_costs_and_stack(host,proposal,response)
    if finish_conditional_cost_returns(host,waiting) or result is True and not waiting or host._stabilize():
        return
    from .model import YieldPolicy
    host.state.priority_player = proposal.seat
    host.state.priority_passes = []
    host.state.players[proposal.seat].yield_policy = YieldPolicy()


def issue_conditional_return_choice(host: Any, required: 'ConditionalReturnChoiceRequired') -> None:
    from .replacement.ordering import replacement_choice_payload
    chooser = required.pending.choice.chooser
    host.permissions.issue(kind='replacement.order',role='pilot',actors=[chooser],allowed_actions=['choose'],
        payload_by_actor={chooser:replacement_choice_payload(required.pending,required.effects)},
        continuation={'replacement_resume_kind':'conditional_return',
            'conditional_bindings':[binding.to_dict() for binding in required.bindings],
            'conditional_departures':[source.to_dict() for source in required.departed_sources],
            'conditional_aura_targets':dict(required.aura_targets),
            'replacement_selections':list(required.selections),'replacement_batch':required.batch.to_dict(),
            'replacement_effects':[effect.to_dict() for effect in required.effects]})


def decode_conditional_return(continuation_type, value, batch, effects):
    from .replacement.immutable import FrozenMap
    from .replacement.model import ReplacementEffectError
    from .replacement.replay import _decode_combat_selections
    if any(event.kind != 'zone.change' or event.payload.get('origin') != 'exile'
           or event.payload.get('destination') != 'battlefield' for event in batch.events):
        raise ReplacementEffectError('Conditional return continuation has an invalid movement')
    try:
        bindings=tuple(ConditionalExileBinding.from_dict(item) for item in value['conditional_bindings'])
        departures=tuple(ContinuousObjectIdentity.from_dict(item) for item in value['conditional_departures'])
        if not bindings or not departures or any(binding.source not in departures for binding in bindings):
            raise ValueError('Conditional return bindings differ from specified departures')
        targets=value['conditional_aura_targets']
        held_ids={identity.object_id for binding in bindings for identity in binding.objects}
        if not isinstance(targets,Mapping) or set(targets)-held_ids or any(type(ref) is not str or not ref for ref in targets.values()):
            raise ValueError('Conditional return Aura selections are malformed')
    except (TypeError,ValueError,KeyError) as exc:
        raise ReplacementEffectError(str(exc)) from exc
    return continuation_type(batch=batch,effects=effects,resume_kind='conditional_return',
        effect=FrozenMap({'bindings':[item.to_dict() for item in bindings],
            'departures':[item.to_dict() for item in departures], 'aura_targets':dict(value['conditional_aura_targets'])}),
        replacement_selections=_decode_combat_selections(value))


def resume_conditional_return(host: Any, restored: Any, selection: Any) -> None:
    from .replacement.immutable import thaw_value
    values=restored.thaw_effect()
    bindings=tuple(ConditionalExileBinding.from_dict(item) for item in values['bindings'])
    departures=tuple(ContinuousObjectIdentity.from_dict(item) for item in values['departures'])
    try:
        return_after_source_departures(host,bindings,departures,
            replacement_selections=tuple(thaw_value(item) for item in restored.replacement_selections)+(selection,),
            aura_target_refs=values['aura_targets'])
    except ConditionalReturnChoiceRequired as required:
        issue_conditional_return_choice(host,required)
        return
    except ConditionalReturnAuraChoiceRequired as required:
        issue_conditional_aura_choice(host,required)
        return
    if not host._stabilize():
        host._grant_priority(host.state.active_player)


def issue_conditional_aura_choice(host: Any, required: Any, parent: Mapping[str, Any] | None = None) -> None:
    plan=required.plan
    from .aura.model import SimpleEnchantSpec, enchant_spec_to_dict
    from .enchant_spec import AuraEnchantSubject, TypedEnchantSpec
    player=bool(isinstance(plan.spec,TypedEnchantSpec) and plan.spec.subject is AuraEnchantSubject.PLAYER)
    refs=list(plan.legal_target_refs)
    host.permissions.issue(kind='aura.entry',role='pilot',actors=[plan.controller],allowed_actions=['choose'],
        payload_by_actor={plan.controller:{'aura':host.state.cards[plan.source_object_id].ref,
            'prompt':'Choose a legal subject for this Aura to enchant.',
            'target_schema':{**plan.spec.target_schema(),'legal_refs':refs},
            'legal_actions':[{'id':'choose','action':'choose','choice_schema':{'aura_target':{
                'type':'seat' if player else 'object_ref','required':True,
                ('legal_seats' if player else 'legal_refs'):refs}}}]}},
        continuation={'schema_version':1,'conditional_aura_return':True,
            'source_object_id':plan.source_object_id,'source_logical_object_id':plan.source_logical_object_id,
            'controller':plan.controller,'spec':plan.spec.to_dict() if isinstance(plan.spec,SimpleEnchantSpec) else enchant_spec_to_dict(plan.spec),
            'advertised_targets':refs,'bindings':[item.to_dict() for item in required.bindings],
            'departures':[item.to_dict() for item in required.departed_sources],
            'selections':list(required.selections),'aura_targets':required.aura_targets,'parent':dict(parent) if parent else None})


def resume_conditional_aura_choice(host: Any, decision: Any, *, error_type: type[Exception]) -> None:
    from .aura import legal_aura_target_refs
    from .aura.model import SimpleEnchantSpec, enchant_spec_from_dict
    data=decision.continuation
    expected={'schema_version','conditional_aura_return','source_object_id','source_logical_object_id','controller',
        'spec','advertised_targets','bindings','departures','selections','aura_targets','parent'}
    if set(data)!=expected or type(data['schema_version']) is not int or data['schema_version']!=1 or data['conditional_aura_return'] is not True:
        raise error_type('Conditional Aura return continuation is malformed')
    if data['parent'] is not None:
        parent=data['parent']
        if not isinstance(parent,Mapping) or set(parent)!={'stack_ref','remaining','destination','note','instruction_pointer','semantic_frame'}:
            raise error_type('Conditional Aura return parent frame is malformed')
        item=next((value for value in host.state.stack if value.ref==parent['stack_ref']),None)
        if item is None:raise error_type('Conditional Aura return parent disappeared')
        frame={**dict(parent['semantic_frame']),'pending_choice_id':decision.decision_id}
        host._validate_semantic_frame(frame,item)
    aura=host.state.cards.get(data['source_object_id']);actor=decision.actors[0]
    if aura is None or aura.zone!='exile' or aura.logical_object_id!=data['source_logical_object_id'] or actor!=data['controller']:
        raise error_type('Conditional Aura return identity or controller changed')
    spec=SimpleEnchantSpec.from_dict(data['spec']) if 'object_kind' in data['spec'] else enchant_spec_from_dict(data['spec'])
    selected=decision.responses[actor].get('aura_target')
    if selected not in data['advertised_targets'] or selected not in legal_aura_target_refs(host,aura,spec,controller=actor,as_target=False):
        raise error_type('Conditional Aura return subject is no longer legal')
    targets={**data['aura_targets'],aura.object_id:selected}
    bindings=tuple(ConditionalExileBinding.from_dict(item) for item in data['bindings'])
    departures=tuple(ContinuousObjectIdentity.from_dict(item) for item in data['departures'])
    try:
        return_after_source_departures(host,bindings,departures,replacement_selections=data['selections'],aura_target_refs=targets)
    except ConditionalReturnAuraChoiceRequired as required:
        issue_conditional_aura_choice(host,required,data['parent']);return
    except ConditionalReturnChoiceRequired as required:
        if data['parent'] is not None:
            parent=data['parent'];item=next(value for value in host.state.stack if value.ref==parent['stack_ref'])
            from .replacement_decisions import issue_replacement_order_choice
            issue_replacement_order_choice(host,item=item,effect=required.return_effect(),remaining=parent['remaining'],
                destination=parent['destination'],note=parent['note'],instruction_pointer=parent['instruction_pointer'],required=required)
        else:issue_conditional_return_choice(host,required)
        return
    if data['parent'] is not None:
        parent=data['parent'];host._continue_resolution(stack_ref=parent['stack_ref'],effects=parent['remaining'],
            destination=parent['destination'],note=parent['note'],instruction_pointer=parent['instruction_pointer']+1)
    elif not host._stabilize():host._grant_priority(host.state.active_player)


class ConditionalReturnChoiceRequired(ReplacementChoiceRequired):
    """The source departure committed; only its subsequent return is waiting."""

    def __init__(self, required: ReplacementChoiceRequired, bindings, departed_sources, selections):
        super().__init__(batch=required.batch, effects=required.effects, pending=required.pending)
        self.bindings = tuple(bindings)
        self.departed_sources = tuple(departed_sources)
        self.selections = tuple(selections)
        self.aura_targets = {}
        self.return_objects = {}

    def return_effect(self) -> dict[str, Any]:
        from .linked_exile_return_model import LinkedExileReturnSpec
        identities = self.return_objects
        return {'op': 'return_linked_exiled_objects', 'objects': [value.to_dict() for value in identities.values()],
            'spec': LinkedExileReturnSpec().to_dict(), '_replacement_selections': list(self.selections),
            **({'_aura_target_refs':dict(self.aura_targets)} if self.aura_targets else {})}


class ConditionalReturnAuraChoiceRequired(AuraEntryChoiceRequired):
    def __init__(self, required, bindings, departures, selections, aura_targets):
        super().__init__(required.plan)
        self.bindings=tuple(bindings);self.departed_sources=tuple(departures)
        self.selections=tuple(selections);self.aura_targets=dict(aura_targets)


def return_from_departure_snapshots(host: Any, snapshots: Sequence[tuple[Any, Any]]) -> tuple[Any, ...]:
    """Use captured instructions after all members of one zone event commit."""
    bindings = tuple(binding for _card, departure in snapshots for binding in departure.conditional_returns)
    if not bindings:
        return ()
    departures = tuple(ContinuousObjectIdentity(card.object_id, departure.logical_object_id)
        for card, departure in snapshots if departure.origin == 'battlefield')
    waiting = _DEFERRED_RETURNS.get()
    if waiting is not None:
        waiting.append((bindings,departures))
        return ()
    return return_after_source_departures(host, bindings, departures)


def return_after_source_departures(
    host: Any, bindings: Sequence[ConditionalExileBinding],
    departed_sources: Sequence[ContinuousObjectIdentity], *,
    replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
    aura_target_refs: Mapping[str, str] | None = None,
) -> tuple[Any, ...]:
    """Commit the second one-shot effect after actual specified departures."""
    try:
        cards = conditional_departure_return_objects(bindings, departed_sources, host.state.cards)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if any(conditional_source_remains(source, host.state.cards) for source in departed_sources):
        raise GameRuleError('Conditional return requires a committed source departure')
    if not cards:
        return ()
    controllers = {card.object_id: card.owner for card in cards}
    if any(owner not in host.active_seats for owner in controllers.values()):
        raise GameRuleError('Conditional return owner is unavailable')
    try:
        return tuple(ZoneTransitionOwner(host).move_cards_simultaneously(
            tuple((card.object_id, 'battlefield') for card in cards),
            reason='conditional exile source left the battlefield',
            destination_controllers=controllers, replacement_selections=replacement_selections,
            aura_target_refs=aura_target_refs,
        ))
    except ReplacementChoiceRequired as required:
        pending=ConditionalReturnChoiceRequired(required, bindings, departed_sources, replacement_selections)
        pending.aura_targets=dict(aura_target_refs or {})
        from .linked_exile_return_model import ExiledObjectIdentity
        pending.return_objects={card.object_id:ExiledObjectIdentity(card.object_id,card.ref,card.logical_object_id) for card in cards}
        raise pending from required
    except AuraEntryChoiceRequired as required:
        raise ConditionalReturnAuraChoiceRequired(required,bindings,departed_sources,replacement_selections,aura_target_refs or {}) from required
