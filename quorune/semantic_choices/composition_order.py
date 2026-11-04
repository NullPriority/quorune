from __future__ import annotations

from dataclasses import dataclass
from ..composition_order_model import CompositionOrderRequest
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.composition_intent import ResumeCompositionOrderIntent
from .model import (OrderingChoice,SemanticChoiceRequest,SemanticChoicePreparation,
    SemanticChoiceCompletion,SemanticChoiceError)


@dataclass(frozen=True,slots=True)
class CompositionOrderHandler:
    operation: str='composition_departure_order'
    handler_id: str='choice.composition.departure-order.v1'
    schema_version: int=1
    rule_references: tuple[str,...]=('730.3','730.3a','730.3b','608.2c')
    capability_dependencies: tuple[str,...]=('zone.composition.departure_order',)
    continuation_fields: tuple[str,...]=('player','request','original_effect')
    private_data: tuple[str,...]=('physical component order','hidden component identities')
    projected_fields: tuple[str,...]=('prompt','objects','legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str,...]=('ResumeCompositionOrderIntent','ZoneTransitionOwner.depart_composition')
    replay_fixture: str='composition-departure-owner-order'
    test_modules: tuple[str,...]=('tests.test_permanent_composition',)

    def prepare(self,effect,context):
        try:request=CompositionOrderRequest.from_dict(effect['request'])
        except (KeyError,ValueError,TypeError) as exc:raise SemanticChoiceError(str(exc)) from exc
        if set(effect)!={'op','player','request','original_effect'} or effect['op']!=self.operation:
            raise SemanticChoiceError('Composition order instruction has unknown fields')
        if context.actor!=request.chooser or context.stack_controller!=request.actor or effect['player']!=request.chooser:
            raise SemanticChoiceError('Composition order actor changed')
        root=context.query.object(request.root_state['ref'],zones=('battlefield',))
        if root is None or root.logical_object_id!=request.root.logical_object_id:
            raise SemanticChoiceError('Composition ordering source changed')
        rows=[]
        for part in request.root.permanent_composition.parts:
            raw=part.card_state
            if raw['ref'] not in request.refs:continue
            hidden=(request.current_destination=='library' and raw['face_down']
                and request.chooser not in (*raw['known_to'],*raw['revealed_to']))
            rows.append({'id':raw['ref'],'name':'Face-down component' if hidden else raw['printed_name']})
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt='Order the physical cards from bottom to top.',
            choice=OrderingChoice('cards',request.refs,visibility='actor_private',
                schema_extras=FrozenMap({'order':'bottom_to_top'})),
            public_context=FrozenMap({'stack':context.stack_ref,'operation':self.operation,'objects':rows})),
            continuation_effect=FrozenMap(effect))

    def complete(self,continuation,response,query):
        try:
            request=CompositionOrderRequest.from_dict(continuation.effect['request'])
            if (set(response)-{'cards','action','choice_schema'} or 'cards' not in response
                    or 'action' in response and response['action']!='choose'):
                raise ValueError('Order response has unsupported fields: '+', '.join(sorted(response)))
            if 'choice_schema' in response:
                expected=OrderingChoice('cards',request.refs,visibility='actor_private',
                    schema_extras=FrozenMap({'order':'bottom_to_top'})).choice_schema()
                if FrozenMap(response['choice_schema'])!=FrozenMap(expected):
                    raise ValueError('Order response schema changed from its issued permutation')
            request.validate_order(response['cards'])
            intent=ResumeCompositionOrderIntent(request,continuation.effect['original_effect'],
                continuation.semantic_frame.to_dict(),tuple(response['cards']))
        except (ValueError,KeyError,TypeError) as exc:raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(intents=(intent,))
