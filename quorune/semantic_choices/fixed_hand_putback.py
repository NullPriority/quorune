from __future__ import annotations

"""Private exact current-hand selection with one shared library placement."""

from dataclasses import dataclass
from ..fixed_hand_putback_model import (
    FIXED_HAND_PUTBACK_OPERATION as OP, FIXED_HAND_PUTBACK_CAPABILITY as CAP,
    PutbackPlacement as Placement,FixedHandPutbackOrder,decode_fixed_hand_putback,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .model import (
    AutoContinue,ObjectChoice,ScalarChoice,SemanticChoiceRequest,
    SemanticChoicePreparation,SemanticChoiceCompletion,SemanticChoiceError,
)

_BASE=frozenset({'op','player','putback'})
_SAVED=frozenset({'_stage','_choice_actor','_hand_incarnations','_stack_label'})


def _base(effect):
    if not _BASE<=set(effect) or set(effect)-_BASE-{'_placement'}-_SAVED:
        raise SemanticChoiceError('Fixed putback instruction fields changed')
    return {key:effect[key] for key in _BASE}


@dataclass(frozen=True,slots=True)
class FixedHandPutbackHandler:
    operation: str=OP
    handler_id: str='choice.hand.fixed-putback.v1'
    schema_version: int=1
    rule_references: tuple[str,...]=('121.2','400.7','401.4','608.2c','609.3','701.24c','903.9b')
    capability_dependencies: tuple[str,...]=(CAP,'zone.change.destination_replacement')
    continuation_fields: tuple[str,...]=('op','player','putback','_placement',*_SAVED)
    private_data: tuple[str,...]=('current owner hand','selected private permutation')
    projected_fields: tuple[str,...]=('prompt','objects','legal_actions.choice_schema')
    mutation_path: tuple[str,...]=('MoveObjectsSimultaneouslyIntent','ZoneTransitionOwner.move_cards_simultaneously','canonical library shuffle')
    replay_fixture: str='fixed-draw-hand-putback'
    test_modules: tuple[str,...]=('tests.test_fixed_draw_hand_putback',)

    def prepare(self,effect,context):
        try:
            base=_base(effect);spec=decode_fixed_hand_putback(base,player=context.actor)
            if (context.actor!=context.stack_controller or context.actor not in context.query.active_seats
                    or set(effect)-set(base)-{'_placement'}):
                raise ValueError('Fixed putback requires its active source controller')
            rows=tuple(sorted(context.query.objects(zones=('hand',),owner=context.actor),key=lambda row:row.ref))
            count=spec.required(len(rows))
            if not count:
                return SemanticChoicePreparation(None,FrozenMap(effect),auto_continue=AutoContinue(reason='No hand card can be put back'))
            if spec.placement is Placement.EITHER and '_placement' not in effect:
                return SemanticChoicePreparation(
                    request=SemanticChoiceRequest(prompt='Put the whole group on top or on the bottom of your library.',
                        choice=ScalarChoice('placement',legal_values=('top','bottom')),
                        public_context=FrozenMap({'stack':context.stack_ref,'operation':OP})),
                    continuation_effect=FrozenMap({**base,'_stage':'placement','_choice_actor':context.actor}))
            position=spec.resolved_placement(effect.get('_placement'))
            return SemanticChoicePreparation(
                request=SemanticChoiceRequest(
                    prompt=('Choose a card from your hand to shuffle into your library.' if position=='shuffle'
                            else 'Choose the required cards and order them nearest the library edge first.'),
                    choice=ObjectChoice('cards',tuple(row.ref for row in rows),('hand',),
                        minimum=count,maximum=count,visibility='actor_private',owner_relation='actor',
                        schema_extras=FrozenMap({'order':'top_to_bottom' if position=='top' else 'bottom_to_top'} if position!='shuffle' else {})),
                    public_context=FrozenMap({'stack':context.stack_ref,'operation':OP,
                        'objects':[{'id':row.ref,'name':row.printed_name} for row in rows]})),
                continuation_effect=FrozenMap({**dict(effect),'_stage':'cards','_choice_actor':context.actor,
                    '_hand_incarnations':{row.ref:row.logical_object_id for row in rows},'_stack_label':context.stack_label}))
        except (ValueError,TypeError,KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self,continuation,response,query):
        try:
            effect=continuation.effect;base=_base(effect);actor=effect['_choice_actor']
            spec=decode_fixed_hand_putback(base,player=actor)
            if actor!=continuation.semantic_frame.controller or actor not in query.active_seats:
                raise ValueError('Fixed putback controller changed')
            if effect['_stage']=='placement':
                if set(effect)!=set(base)|{'_stage','_choice_actor'} or spec.placement is not Placement.EITHER:
                    raise ValueError('Putback placement continuation changed')
                position=spec.resolved_placement(response.get('placement'))
                return SemanticChoiceCompletion(repeat_effect=FrozenMap({**base,'_placement':position}))
            expected=set(base)|_SAVED|({'_placement'} if spec.placement is Placement.EITHER else set())
            if effect['_stage']!='cards' or set(effect)!=expected:
                raise ValueError('Putback hand continuation changed')
            position=spec.resolved_placement(effect.get('_placement'))
            rows=query.objects(zones=('hand',),owner=actor)
            if {row.ref:row.logical_object_id for row in rows}!=effect['_hand_incarnations']:
                raise ValueError('Putback current hand incarnations changed')
            refs=response.get('cards')
            if (not isinstance(refs,(list,tuple)) or any(type(ref) is not str or not ref for ref in refs)
                    or len(refs)!=len(set(refs)) or len(refs)!=spec.required(len(rows))
                    or not set(refs)<={row.ref for row in rows}):
                raise ValueError('Choose the required distinct current hand cards')
            order=FixedHandPutbackOrder(actor,position,tuple(refs)) if position!='shuffle' else None
            move=MoveObjectsSimultaneouslyIntent(actor,tuple(refs),('hand',),'library',
                effect['_stack_label'],owned_only=True,library_order=order)
            shuffle=(FrozenMap({'op':'shuffle_controller_library','player':actor}),) if position=='shuffle' else ()
            return SemanticChoiceCompletion(intents=(move,),prepend_effects=shuffle)
        except (ValueError,TypeError,KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
