from __future__ import annotations

"""Private hand selection/order followed by one result-bound return transaction."""

from dataclasses import dataclass
from collections.abc import Mapping
from ..hand_library_redraw_model import (
    HAND_LIBRARY_REDRAW_OPERATION as OP, HAND_LIBRARY_REDRAW_CAPABILITY as CAP,
    HAND_LIBRARY_REDRAW_DEPENDENCIES as DEPENDENCIES, HandLibraryPlacement as Placement,
    HandLibrarySelection as Selection, HandLibraryScope as Scope,
    HandLibraryRedrawIntent, HandLibraryResultDraws, decode_hand_library_redraw,
)
from ..replacement.immutable import FrozenMap
from ..rules.hand_library_redraw import hand_library_instruction
from .model import (
    AutoContinue, ObjectChoice, SemanticChoicePreparation, SemanticChoiceCompletion,
    SemanticChoiceRequest, SemanticChoiceError,
)


def _automatic(effect, context, spec, hands, selected):
    players = tuple(context.query.apnap_order) if spec.scope is Scope.ALL_PLAYERS else (context.actor,)
    intent = HandLibraryRedrawIntent(context.stack_controller,players,spec,hands,selected,context.stack_label)
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect), preparation_intents=(intent,),
        auto_continue=AutoContinue(reason='Hand returns commit before result-bound draws',
                                  player_draws_from_result=HandLibraryResultDraws(0,offset=spec.draw_offset)))


@dataclass(frozen=True, slots=True)
class HandLibraryRedrawHandler:
    operation: str = OP
    handler_id: str = 'choice.hand.library-redraw.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('121.2','121.2c','400.7','401.4','608.2c','609.3','614.6','701.24c','701.24d','903.9b')
    capability_dependencies: tuple[str, ...] = DEPENDENCIES
    continuation_fields: tuple[str, ...] = (
        'op','actor','player','redraw','_hands','_selected','_choice_actor','_hand_incarnations','_stack_label',
    )
    private_data: tuple[str, ...] = ('HandLibraryRedrawIntent.hands','private selected hand permutation')
    projected_fields: tuple[str, ...] = ('prompt','objects','legal_actions.choice_schema')
    mutation_path: tuple[str, ...] = ('MoveObjectsSimultaneouslyIntent','ZoneTransitionOwner.move_cards_simultaneously','canonical shuffle and draw owners')
    replay_fixture: str = 'hand-library-redraw'
    test_modules: tuple[str, ...] = ('tests.test_hand_library_redraw',)

    def prepare(self, effect, context):
        try:
            base = hand_library_instruction(effect)
            spec = decode_hand_library_redraw(base,actor=context.stack_controller,player=context.actor)
            hands = context.query.discard_hand_inputs
            if hands is None:
                raise ValueError('Authoritative hand membership is unavailable')
            if not hands:
                if spec.scope is not Scope.EVENT_PLAYER:
                    raise ValueError('Hand redraw has no represented recipients')
                return SemanticChoicePreparation(None,FrozenMap(effect),auto_continue=AutoContinue(reason='Draw-step participant left'))
            players = tuple(context.query.apnap_order) if spec.scope is Scope.ALL_PLAYERS else (context.actor,)
            if set(hands) != set(players):
                raise ValueError('Hand redraw snapshot recipients changed')
            if '_selected' in effect:
                if set(effect) != set(base) | {'_hands','_selected'} or FrozenMap(effect['_hands']) != hands:
                    raise ValueError('Selected hand identities changed')
                return _automatic(effect,context,spec,hands,FrozenMap(effect['_selected']))
            if set(effect) != set(base):
                raise ValueError('Hand redraw preparation has unknown retained fields')
            rows = tuple(sorted(context.query.objects(zones=('hand',),owner=context.actor),key=lambda row:row.ref))
            automatic = (spec.placement is Placement.SHUFFLE and spec.selection is Selection.ALL
                         or not rows or spec.selection is Selection.ALL and len(rows) == 1)
            if automatic:
                selected = FrozenMap({seat:tuple(member['ref'] for member in hands[seat]) for seat in players})
                return _automatic(effect,context,spec,hands,selected)
            if len(players) != 1 or context.actor not in context.query.active_seats:
                raise ValueError('Private hand selection requires its active owner')
            minimum = len(rows) if spec.selection is Selection.ALL else 0
            return SemanticChoicePreparation(
                request=SemanticChoiceRequest(
                    prompt=('Choose cards from your hand and order them bottom card first.' if spec.placement is Placement.BOTTOM
                            else 'Choose any number of cards from your hand to shuffle into your library.'),
                    choice=ObjectChoice(field_name='cards',legal_refs=tuple(row.ref for row in rows),zones=('hand',),
                        minimum=minimum,maximum=len(rows),optional=spec.selection is Selection.ANY,
                        visibility='actor_private',owner_relation='actor',
                        schema_extras=FrozenMap({'order':'bottom_to_top'} if spec.placement is Placement.BOTTOM else {})),
                    public_context=FrozenMap({'stack':context.stack_ref,'operation':OP,
                        'objects':[{'id':row.ref,'name':row.printed_name} for row in rows]})),
                continuation_effect=FrozenMap({**base,'_choice_actor':context.actor,'_hands':hands,
                    '_hand_incarnations':{row.ref:row.logical_object_id for row in rows},'_stack_label':context.stack_label}))
        except (ValueError,TypeError,KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        try:
            effect = continuation.effect
            base = hand_library_instruction(effect)
            actor = effect['_choice_actor']
            spec = decode_hand_library_redraw(base,actor=continuation.semantic_frame.controller,player=actor)
            if set(effect) != set(base) | {'_choice_actor','_hands','_hand_incarnations','_stack_label'}:
                raise ValueError('Hand redraw continuation fields changed')
            refs = response.get('cards',())
            identities = effect['_hand_incarnations']
            if (not isinstance(refs,(list,tuple)) or any(type(ref) is not str or not ref for ref in refs)
                    or len(refs) != len(set(refs)) or not set(refs) <= set(identities)
                    or spec.selection is Selection.ALL and set(refs) != set(identities)
                    or actor not in query.active_seats):
                raise ValueError('Choose the represented distinct hand cards and order')
            rows = query.objects(zones=('hand',),owner=actor)
            if {row.ref:row.logical_object_id for row in rows} != identities:
                raise ValueError('Private hand identities changed')
            selected = {actor:list(refs)}
            # Validate complete frozen hands and subset before sealing the next instruction.
            HandLibraryRedrawIntent(base['actor'],(actor,),spec,effect['_hands'],FrozenMap(selected),effect['_stack_label'])
            return SemanticChoiceCompletion(prepend_effects=(FrozenMap({
                **base,'_hands':effect['_hands'],'_selected':selected}),))
        except (ValueError,TypeError,KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
