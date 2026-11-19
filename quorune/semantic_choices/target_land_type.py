"""The resolving controller chooses a public basic type for one original land."""
from dataclasses import dataclass
from ..target_land_type_model import (
    TARGET_LAND_TYPE_OPERATION,TARGET_LAND_TYPE_CAPABILITY,
    decode_target_land_type,target_land_type_choice_fields,target_land_type_options,TargetLandTypeSettingIntent,
)
from ..replacement.immutable import FrozenMap
from .model import (AutoContinue,ScalarChoice,SemanticChoiceCompletion,SemanticChoiceError,
    SemanticChoicePreparation,SemanticChoiceRequest)

_FIELDS={'op','schema_version','card','player'}
_LOCAL={'_target_identity','_source_context'}
_RESPONSE={'land_type','action','action_id','a','cap','reason','plan','next','choice_schema','label'}


def _land(query,ref):
    row=query.object(ref,zones=('battlefield',))
    return row if row is not None and not row.phased_out and 'land' in row.types else None


@dataclass(frozen=True,slots=True)
class TargetLandTypeChoiceHandler:
    operation:str=TARGET_LAND_TYPE_OPERATION
    handler_id:str='choice.public.target-basic-land-type.v1'
    schema_version:int=1
    rule_references:tuple[str,...]=('CR 205.3i','CR 305.6','CR 305.7','CR 608.2b','CR 608.2d','CR 611.2c','CR 613.1d')
    capability_dependencies:tuple[str,...]=(TARGET_LAND_TYPE_CAPABILITY,)
    continuation_fields:tuple[str,...]=tuple(sorted(_FIELDS|_LOCAL|{'controller','choices'}))
    private_data:tuple[str,...]=()
    projected_fields:tuple[str,...]=('prompt','legal_actions.choice_schema.legal_values')
    mutation_path:tuple[str,...]=('TargetLandTypeSettingIntent','continuous_effect_state.create_resolution_continuous_effect')
    replay_fixture:str='target-basic-land-type-choice'
    test_modules:tuple[str,...]=('tests.test_target_land_type_choices',)

    def prepare(self,effect,context):
        try:ref,actor=decode_target_land_type(effect,materialized=True)
        except (TypeError,ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
        if actor!=context.actor or actor!=context.stack_controller:
            raise SemanticChoiceError('The resolving controller chooses the basic land type')
        target=_land(context.query,ref)
        if effect['schema_version']==2 and target is not None and target.controller!=actor:
            raise SemanticChoiceError('The land must be controlled by the resolving player')
        source={'source_stack_ref':context.stack_ref,'source_object_id':context.source_object_id,
            'source_logical_object_id':context.source_logical_object_id,'source_card_ref':context.source_ref}
        local={**dict(effect),'_target_identity':{'object_id':target.object_id,'logical_object_id':target.logical_object_id} if target else None,
            '_source_context':source}
        if target is None:
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(local),auto_continue=AutoContinue(reason='The original land is unavailable'))
        return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Choose a basic land type.',
            choice=ScalarChoice(field_name='land_type',legal_values=target_land_type_options(effect)),
            public_context=FrozenMap({'stack':context.stack_ref,'operation':self.operation})),continuation_effect=FrozenMap(local))

    def complete(self,continuation,response,query):
        effect=continuation.effect
        fields=target_land_type_choice_fields(effect.get('schema_version'))
        if set(effect)!=fields|_LOCAL or set(response)-_RESPONSE:
            raise SemanticChoiceError('Basic type choice fields changed')
        try:ref,actor=decode_target_land_type({k:effect[k] for k in fields},materialized=True)
        except (TypeError,ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
        stack=query.stack_object(continuation.stack_ref)
        if stack is None or stack.controller!=actor or actor!=continuation.semantic_frame.controller or actor not in query.active_seats:
            raise SemanticChoiceError('Basic type chooser no longer controls the resolving object')
        chosen=response.get('land_type')
        if type(chosen) is not str or chosen not in target_land_type_options(effect):
            raise SemanticChoiceError('Choose exactly one offered basic land type')
        identity=effect['_target_identity'];target=_land(query,ref)
        if identity is None or target is None:return SemanticChoiceCompletion()
        if (target.object_id,target.logical_object_id)!=(identity.get('object_id'),identity.get('logical_object_id')):
            raise SemanticChoiceError('The basic type recipient changed incarnation')
        if effect['schema_version']==2 and target.controller!=actor:
            raise SemanticChoiceError('The land is no longer controlled by the resolving player')
        source=effect['_source_context']
        if set(source)!={'source_stack_ref','source_object_id','source_logical_object_id','source_card_ref'} or source['source_stack_ref']!=continuation.stack_ref:
            raise SemanticChoiceError('The basic type instruction lost its original stack source')
        try:intent=TargetLandTypeSettingIntent(actor,ref,target.object_id,target.logical_object_id,chosen.casefold(),continuation.stack_ref,
            'Resolve basic land type choice',source['source_object_id'],source['source_logical_object_id'],source['source_card_ref'],
            actor if effect['schema_version']==2 else None)
        except (TypeError,ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(intents=(intent,))
