"""Controller-private colored subset followed by publicly revealed fixed results."""
from dataclasses import dataclass
from ..colored_hand_reveal_model import (
    COLORED_REVEAL_OPERATION as OP, COLORED_REVEAL_CAPABILITY as CAP,
    MatchingManaValueHandRevealSpec, decode_colored_hand_reveal,
)
from ..fixed_token_production import treasure_token_effect
from ..public_quantity_reductions import public_mana_value
from ..rules.colored_hand_reveals import colored_reveal_result_dependencies
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.colored_hand_reveal import RevealColoredHandCardsIntent
from .model import (AutoContinue,ObjectChoice,SemanticChoiceRequest,SemanticChoicePreparation,
    SemanticChoiceCompletion,SemanticChoiceError)

_BASE={'op','player','reveal'}
_SAVED={'_choice_actor','_hand_incarnations','_source_ref'}


def _spec(effect,actor):
    if not _BASE<=set(effect) or set(effect)-_BASE-_SAVED:raise ValueError('Colored reveal continuation fields changed')
    base={key:effect[key] for key in _BASE};spec=decode_colored_hand_reveal(base,player=actor)
    colored_reveal_result_dependencies(spec)
    return base,spec


def _matches_hand_row(row, spec):
    if isinstance(spec, MatchingManaValueHandRevealSpec):
        return row.physical_card and not row.token and "land" not in row.types and row.known_to_actor
    return spec.color in row.colors and not row.token


def _hand_snapshot(row, actor, *, matching_values):
    snapshot = {"object_id":row.object_id, "logical_object_id":row.logical_object_id, "ref":row.ref,
                "player":actor, "name":row.printed_name}
    if matching_values:
        snapshot["mana_value"] = public_mana_value({"mana_value":row.mana_value})
    return FrozenMap(snapshot)


def _hand_reveal_followups(spec, snapshots):
    if isinstance(spec, MatchingManaValueHandRevealSpec):
        total = spec.matching_count(tuple(row["mana_value"] for row in snapshots))
        return (FrozenMap(treasure_token_effect(total)),) if total else ()
    return (FrozenMap(spec.result.materialize(len(snapshots))),)


@dataclass(frozen=True,slots=True)
class ColoredHandRevealChoiceHandler:
    operation: str=OP
    handler_id: str='choice.hand.colored-reveal.v1'
    schema_version: int=1
    rule_references: tuple[str,...]=('400.7','608.2b','608.2h','701.20a','701.20b')
    capability_dependencies: tuple[str,...]=(CAP,)
    continuation_fields: tuple[str,...]=tuple(sorted(_BASE|_SAVED))
    private_data: tuple[str,...]=('current own hand color candidates','original hand incarnations')
    projected_fields: tuple[str,...]=('prompt','objects','legal_actions.choice_schema')
    mutation_path: tuple[str,...]=('RevealColoredHandCardsIntent','commit_selected_hand_reveal','ordinary result intents')
    replay_fixture: str='colored-hand-reveal-results'
    test_modules: tuple[str,...]=('tests.test_colored_hand_reveals',)

    def prepare(self,effect,context):
        try:
            base,spec=_spec(effect,context.actor)
            if set(effect)!=_BASE or context.actor!=context.stack_controller or context.actor not in context.query.active_seats:
                raise ValueError('Colored hand reveal requires its active stack controller')
            rows=tuple(sorted(context.query.objects(zones=('hand',),owner=context.actor),key=lambda row:row.ref))
            legal=tuple(row for row in rows if _matches_hand_row(row, spec))
            if not legal:
                return SemanticChoicePreparation(None,FrozenMap(base),auto_continue=AutoContinue(
                    reason='No matching hand card can be revealed',prepend_effects=_hand_reveal_followups(spec, ())))
            matching_values = isinstance(spec, MatchingManaValueHandRevealSpec)
            maximum = min(len(legal), spec.maximum) if matching_values else len(legal)
            prompt = ("Choose up to five nonland cards in your hand to reveal." if matching_values
                      else "Choose any number of matching cards in your hand to reveal.")
            source=context.source_ref or context.card_ref or context.stack_ref
            return SemanticChoicePreparation(
                request=SemanticChoiceRequest(prompt=prompt,
                    choice=ObjectChoice('cards',tuple(row.ref for row in legal),('hand',),minimum=0,maximum=maximum,
                        optional=True,visibility='actor_private',owner_relation='actor'),
                    public_context=FrozenMap({'stack':context.stack_ref,'operation':OP,
                        'objects':[{'id':row.ref,'name':row.printed_name} for row in legal]})),
                continuation_effect=FrozenMap({**base,'_choice_actor':context.actor,'_source_ref':source,
                    '_hand_incarnations':{row.ref:row.logical_object_id for row in rows}}))
        except (ValueError,TypeError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc

    def complete(self,continuation,response,query):
        try:
            effect=continuation.effect;actor=effect['_choice_actor'];base,spec=_spec(effect,actor)
            if set(effect)!=_BASE|_SAVED or actor!=continuation.semantic_frame.controller or actor not in query.active_seats:
                raise ValueError('Colored reveal controller or original continuation changed')
            rows=tuple(query.objects(zones=('hand',),owner=actor))
            if {row.ref:row.logical_object_id for row in rows}!=effect['_hand_incarnations']:
                raise ValueError('Colored reveal original hand incarnations changed')
            refs=response.get('cards');legal={row.ref:row for row in rows if _matches_hand_row(row, spec)}
            matching_values = isinstance(spec, MatchingManaValueHandRevealSpec)
            if (not isinstance(refs,(list,tuple)) or any(type(ref) is not str for ref in refs)
                    or len(refs)!=len(set(refs)) or not set(refs)<=set(legal)
                    or matching_values and len(refs) > spec.maximum):
                raise ValueError('Choose distinct current matching hand cards')
            snapshots = tuple(_hand_snapshot(legal[ref], actor, matching_values=matching_values) for ref in refs)
            color = None if matching_values else spec.color
            intents=(RevealColoredHandCardsIntent(actor,effect['_source_ref'],color,snapshots),) if refs else ()
            return SemanticChoiceCompletion(intents=intents,prepend_effects=_hand_reveal_followups(spec, snapshots))
        except (ValueError,TypeError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
