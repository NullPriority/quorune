"""Champion's optional public exile choice and explicit linked return adapter."""
from __future__ import annotations

from dataclasses import dataclass

from ..champion_model import ChampionSpec, champion_candidates, CHAMPION_CAPABILITY, CHAMPION_CHOICE_OPERATION
from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.champion_intent import ChampionExileIntent, ReturnLinkedAbilityExileIntent
from .model import (AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)


CHAMPION_RETURN_OPERATION = 'return_champion_exile'


def _source(context):
    try:
        return ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)
    except ValueError as exc:
        raise SemanticChoiceError('Champion lost its original source incarnation') from exc


def _spec(effect):
    try:
        return ChampionSpec.from_dict(thaw_value(effect['spec']))
    except (ValueError, KeyError, TypeError) as exc:
        raise SemanticChoiceError('Champion has malformed subject data') from exc


def _validate(effect, operation):
    fields = {'op','ability_pair'} | ({'spec'} if operation == CHAMPION_CHOICE_OPERATION else set())
    retained = {'_champion_source','_champion_actor','_champion_reason','_champion_offered'}
    if set(effect) not in (fields, fields | retained) or effect.get('op') != operation:
        raise SemanticChoiceError('Champion instruction has an invalid shape')
    if (type(effect.get('ability_pair')) is not str or not effect['ability_pair']
            or effect['ability_pair'] != effect['ability_pair'].strip()):
        raise SemanticChoiceError('Champion requires its linked ability pair')


@dataclass(frozen=True, slots=True)
class ChampionExileChoiceHandler:
    operation: str = CHAMPION_CHOICE_OPERATION
    handler_id: str = 'choice.public.champion-exile.v1'
    schema_version: int = 1
    rule_references: tuple[str,...] = ('CR 702.72a','CR 702.72b','CR 607.1','CR 607.2k','CR 701.21a')
    capability_dependencies: tuple[str,...] = (CHAMPION_CAPABILITY,)
    continuation_fields: tuple[str,...] = ('ability_pair','spec','_champion_source','_champion_actor','_champion_reason','_champion_offered')
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ('prompt','legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str,...] = ('ChampionExileIntent','champion_state.exile_champion_permanent')
    replay_fixture: str = 'champion-linked-exile'
    test_modules: tuple[str,...] = ('tests.test_champion_choices',)

    def prepare(self, effect, context):
        _validate(effect,self.operation)
        if '_champion_actor' in effect and effect['_champion_actor'] != context.actor:
            raise SemanticChoiceError('A retained Champion trigger controller changed')
        source = (ContinuousObjectIdentity.from_dict(thaw_value(effect['_champion_source']))
                  if '_champion_source' in effect else _source(context))
        spec = _spec(effect)
        candidates = champion_candidates(context.query.objects(zones=('battlefield',)),
            spec=spec, actor=context.actor, source=source)
        stored = FrozenMap({'op':self.operation,'ability_pair':effect['ability_pair'],'spec':spec.to_dict(),
            '_champion_source':source.to_dict(),'_champion_actor':context.actor,
            '_champion_reason':context.stack_label,
            '_champion_offered':{row.ref:{'object_id':row.object_id,'logical_object_id':row.logical_object_id}
                                  for row in candidates}})
        if not candidates:
            return SemanticChoicePreparation(request=None,continuation_effect=stored,
                preparation_intents=(ChampionExileIntent(context.actor,source,effect['ability_pair'],spec,
                                                         None,None,context.stack_label),),
                auto_continue=AutoContinue(reason='No qualifying Champion exile is possible'))
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt='Exile another qualifying permanent you control, or decline and sacrifice the Champion.',
            choice=ObjectChoice(field_name='cards',legal_refs=tuple(row.ref for row in candidates),
                zones=('battlefield',),minimum=0,maximum=1,optional=True,visibility='public',
                owner_relation='any',controller_relation='actor'),
            public_context=FrozenMap({'operation':self.operation,'stack':context.stack_ref})),
            continuation_effect=stored)

    def complete(self, continuation, response, query):
        effect=continuation.effect;_validate(effect,self.operation)
        if not {'_champion_source','_champion_actor','_champion_reason','_champion_offered'}.issubset(effect):
            raise SemanticChoiceError('Champion completion has no retained source or offers')
        if set(response)-{'cards','action_id','action','choice_schema'} or 'cards' not in response:
            raise SemanticChoiceError('Champion requires its exact public card response')
        refs=response['cards']
        if not isinstance(refs,(list,tuple)) or len(refs)>1 or any(type(ref) is not str for ref in refs):
            raise SemanticChoiceError('Champion chooses at most one exact reference')
        actor=effect['_champion_actor'];source=ContinuousObjectIdentity.from_dict(thaw_value(effect['_champion_source']))
        spec=_spec(effect);subject=None
        if refs:
            offered=effect['_champion_offered'].get(refs[0])
            if offered is None:raise SemanticChoiceError('The Champion choice was not offered')
            subject=ContinuousObjectIdentity.from_dict(thaw_value(offered))
            current=champion_candidates(query.objects(zones=('battlefield',)),spec=spec,actor=actor,source=source)
            if not any(row.ref==refs[0] and ContinuousObjectIdentity(row.object_id,row.logical_object_id)==subject for row in current):
                raise SemanticChoiceError('The selected Champion object is stale or no longer eligible')
        return SemanticChoiceCompletion(intents=(ChampionExileIntent(actor,source,effect['ability_pair'],
            spec,subject,refs[0] if refs else None,effect['_champion_reason']),))


@dataclass(frozen=True, slots=True)
class ChampionReturnChoiceHandler(ChampionExileChoiceHandler):
    operation: str = CHAMPION_RETURN_OPERATION
    handler_id: str = 'choice.public.champion-return.v1'
    continuation_fields: tuple[str,...] = ('ability_pair',)
    mutation_path: tuple[str,...] = ('ReturnLinkedAbilityExileIntent','linked_ability_exile_state.return_linked_ability_exile')

    def prepare(self, effect, context):
        _validate(effect,self.operation)
        if set(effect) != {'op','ability_pair'}:
            raise SemanticChoiceError('A linked return has no unresolved public choice')
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
            preparation_intents=(ReturnLinkedAbilityExileIntent(context.actor,_source(context),
                effect['ability_pair'],context.stack_label),),
            auto_continue=AutoContinue(reason='Resolve the original source ability pair return'))

    def complete(self, continuation, response, query):
        raise SemanticChoiceError('A linked return does not accept a player response')
