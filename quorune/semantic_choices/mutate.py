"""The resolving spell controller chooses the physical merge position."""

from dataclasses import dataclass
from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..mutate_model import MUTATE_CAPABILITY
from ..semantic_runtime.mutate_intent import SelectMutateMergeIntent
from ..permanent_composition import MergePosition
from ..replacement.immutable import FrozenMap
from .model import (ScalarChoice,SemanticChoiceRequest,SemanticChoicePreparation,
    SemanticChoiceCompletion,SemanticChoiceError)


def _objects(query,stack_ref,target_ref):
    source=query.object(stack_ref,zones=('stack',));target=query.object(target_ref,zones=('battlefield',))
    if (source is None or target is None or source.owner!=target.owner
            or 'creature' not in target.types or 'human' in target.subtypes or target.phased_out):
        raise SemanticChoiceError('Mutate choice requires its current same-owner non-Human creature')
    return source,target


@dataclass(frozen=True,slots=True)
class MutateMergeChoiceHandler:
    operation:str='mutate_merge'
    handler_id:str='choice.composition.mutate-merge.v1'
    schema_version:int=1
    rule_references:tuple[str,...]=('112.2','702.140c','702.140d','730.2')
    capability_dependencies:tuple[str,...]=(MUTATE_CAPABILITY,)
    continuation_fields:tuple[str,...]=('target','actor','source_object_id','source_logical_object_id',
        'target_object_id','target_logical_object_id')
    private_data:tuple[str,...]=()
    projected_fields:tuple[str,...]=('prompt','options','legal_actions.choice_schema.legal_values')
    mutation_path:tuple[str,...]=('SelectMutateMergeIntent','ZoneTransitionOwner.commit_composition_merge')
    replay_fixture:str='mutate-spell-controller-merge-choice'
    test_modules:tuple[str,...]=('tests.test_mutate_cast_metadata','tests.test_mutate_cast_native')

    def prepare(self,effect,context):
        if (set(effect)!={'op','target'} or effect['op']!=self.operation
                or context.actor!=context.stack_controller):
            raise SemanticChoiceError('Only the resolving spell controller chooses its Mutate position')
        source,target=_objects(context.query,context.stack_ref,effect['target'])
        if source.controller!=context.actor:
            raise SemanticChoiceError('Mutating spell controller changed')
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt='Put the mutating spell on top or on the bottom of the creature.',
            choice=ScalarChoice('choice',('top','bottom')),
            public_context=FrozenMap({'stack':context.stack_ref,'operation':self.operation,
                'options':[{'id':'top','label':'On top'},{'id':'bottom','label':'On the bottom'}]})),
            continuation_effect=FrozenMap({**effect,'actor':context.actor,
                'source_object_id':source.object_id,'source_logical_object_id':source.logical_object_id,
                'target_object_id':target.object_id,'target_logical_object_id':target.logical_object_id}))

    def complete(self,continuation,response,query):
        effect=continuation.effect
        if (set(effect)!={'op',*self.continuation_fields} or effect['op']!=self.operation
                or effect['actor']!=continuation.semantic_frame.controller
                or continuation.remaining or continuation.semantic_frame.instruction_pointer!=0
                or set(response)-{'action','choice','choice_schema'} or 'choice' not in response
                or response.get('action','choose')!='choose'):
            raise SemanticChoiceError('Mutate response or retained continuation is malformed')
        if ('choice_schema' in response
                and (not isinstance(response['choice_schema'],Mapping)
                    or FrozenMap(response['choice_schema'])!=FrozenMap(ScalarChoice('choice',('top','bottom')).choice_schema()))):
            raise SemanticChoiceError('Mutate response changed its issued choice schema')
        source,target=_objects(query,continuation.stack_ref,effect['target'])
        if (source.controller!=effect['actor']
                or (source.object_id,source.logical_object_id)!=(effect['source_object_id'],effect['source_logical_object_id'])
                or (target.object_id,target.logical_object_id)!=(effect['target_object_id'],effect['target_logical_object_id'])):
            raise SemanticChoiceError('Mutate choice object or controller changed')
        try:
            intent=SelectMutateMergeIntent(effect['actor'],continuation.stack_ref,
                ContinuousObjectIdentity(source.object_id,source.logical_object_id),
                ContinuousObjectIdentity(target.object_id,target.logical_object_id),MergePosition(response['choice']))
        except (TypeError,ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(intents=(intent,))
