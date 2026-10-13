from __future__ import annotations

"""CR 701.50 fixed single-object Connive through existing typed owners."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..connive import CONNIVE_CAPABILITY, CONNIVE_OPERATION, ConnivingObject, FixedConniveSpec
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.connive_intent import ConniveCompletedIntent
from ..semantic_runtime.intents import DrawCardsIntent, MoveObjectsSimultaneouslyIntent, PlaceCountersIntent
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (AutoContinue, ObjectChoice, SemanticChoiceCompletion,
                    SemanticChoiceContinuation, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)


_BASE_FIELDS = {'op', 'card', 'player', 'spec', '_connive_stage', '_conniver'}
_ORDERED_BASE_FIELDS = ('op', 'card', 'player', 'spec', '_connive_stage', '_conniver')


def _phase(effect, stage, **facts):
    return FrozenMap({**{key: effect[key] for key in _ORDERED_BASE_FIELDS},
                      '_connive_stage': stage, **facts})


def _validate(effect, actor):
    try:
        spec = FixedConniveSpec.from_dict(effect['spec'])
        conniver = ConnivingObject.from_dict(effect['_conniver'])
        stage = effect['_connive_stage']
        extra = {'draw': set(), 'discard': set(), 'counter': {'_discards'},
                 'event': {'_nonlands'}, 'choose_discard': {'_hand', '_required', '_stack_label'}}
        if type(stage) is not str or stage not in extra or set(effect) != _BASE_FIELDS | extra[stage]:
            raise ValueError('Connive continuation has unknown or missing fields')
        if effect['op'] != CONNIVE_OPERATION or effect['card'] != conniver.ref or effect['player'] != actor or actor != conniver.controller:
            raise ValueError('Connive continuation recipient binding changed')
        return spec, conniver, stage
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _auto(effect, *, intents=(), next_effect=None, reason):
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect), preparation_intents=tuple(intents),
        auto_continue=AutoContinue(reason=reason, prepend_effects=(next_effect,) if next_effect is not None else ()),
    )


def _discard_preparation(effect, context, spec):
    rows = tuple(sorted(context.query.objects(zones=('hand',), owner=context.actor), key=lambda row: row.ref))
    required = min(spec.count, len(rows))
    if required == 0:
        return _auto(effect, next_effect=_phase(effect, 'event', _nonlands=0), reason='No hand cards can be discarded')
    hand = {row.ref: {'logical_object_id': row.logical_object_id, 'nonland': 'land' not in row.types} for row in rows}
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f'Discard {required} card(s) to connive.',
            choice=ObjectChoice(field_name='cards', legal_refs=tuple(hand), zones=('hand',),
                                minimum=required, maximum=required, visibility='actor_private', owner_relation='actor'),
            public_context=FrozenMap({'stack': context.stack_ref, 'operation': CONNIVE_OPERATION,
                                      'cards': [{'ref': row.ref, 'name': row.printed_name} for row in rows]}),
        ),
        continuation_effect=_phase(effect, 'choose_discard', _hand=hand, _required=required, _stack_label=context.stack_label),
    )


def _counter_preparation(effect, context, conniver, spec):
    discards = effect['_discards']
    if not isinstance(discards, Mapping) or len(discards) > spec.count or any(
        type(key) is not str or not key or type(nonland) is not bool for key, nonland in discards.items()
    ):
        raise SemanticChoiceError('Connive discard result identities are malformed')
    committed = set(context.query.committed_discard_incarnations())
    defined = set(context.query.defined_discard_characteristic_incarnations())
    nonlands = sum(nonland for identity, nonland in discards.items() if identity in committed and identity in defined)
    row = context.query.object(conniver.ref, zones=('battlefield',))
    intents = ()
    if nonlands and row is not None and not row.phased_out and row.logical_object_id == conniver.logical_object_id:
        intents = (PlaceCountersIntent(actor=context.actor, object_refs=(row.ref,), counter_name='+1/+1',
                                       amount=nonlands, reason=context.stack_label, source_ref=conniver.ref),)
    return _auto(effect, intents=intents, next_effect=_phase(effect, 'event', _nonlands=nonlands),
                 reason='Committed Connive discards determine counter placement')


@dataclass(frozen=True, slots=True)
class FixedConniveChoiceHandler:
    operation: str = CONNIVE_OPERATION
    handler_id: str = 'choice.hand.fixed-connive.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 701.50', 'CR 121', 'CR 701.9', 'CR 122')
    capability_dependencies: tuple[str, ...] = (CONNIVE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ('card', 'player', 'spec', '_connive_stage', '_conniver',
                                           '_hand', '_required', '_stack_label', '_discards', '_nonlands')
    private_data: tuple[str, ...] = ('hand',)
    projected_fields: tuple[str, ...] = ('prompt', 'cards', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('typed draw continuation', 'MoveObjectsSimultaneouslyIntent',
                                    'PlaceCountersIntent', 'ConniveCompletedIntent')
    replay_fixture: str = 'fixed-connive'
    test_modules: tuple[str, ...] = ('tests.test_fixed_controller_effect_sequences',)

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext):
        spec, conniver, stage = _validate(effect, context.actor)
        if spec.count == 0:
            return _auto(effect, reason='Connive zero creates no event')
        if stage == 'draw':
            return _auto(effect, intents=(DrawCardsIntent(player=context.actor, count=spec.count,
                                                          reason=context.stack_label, private=True),),
                         next_effect=_phase(effect, 'discard'), reason='Connive draws before selecting discards')
        if stage == 'discard':
            return _discard_preparation(effect, context, spec)
        if stage == 'counter':
            return _counter_preparation(effect, context, conniver, spec)
        if stage == 'event':
            return _auto(effect, intents=(ConniveCompletedIntent(context.actor, conniver, spec.count,
                                                                effect['_nonlands'], context.stack_label),),
                         reason='Connive publishes only after its process finishes')
        raise SemanticChoiceError('Connive preparation cannot repeat a submitted discard choice')

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any], query: SemanticChoiceQuery):
        effect = continuation.effect
        spec, conniver, stage = _validate(effect, effect.get('player'))
        if (stage != 'choose_discard' or 'cards' not in response
                or set(response) - {'cards', 'action_id', 'action', 'choice_schema'}):
            raise SemanticChoiceError('Connive requires its exact discard response')
        submitted = response['cards']
        if not isinstance(submitted, (list, tuple)) or any(type(ref) is not str for ref in submitted):
            raise SemanticChoiceError('Connive discard references are malformed')
        selected = tuple(submitted)
        hand = effect['_hand']
        if not isinstance(hand, Mapping) or any(type(ref) is not str or not ref for ref in hand):
            raise SemanticChoiceError('Connive retained hand choice is malformed')
        if type(effect['_required']) is not int or effect['_required'] != min(spec.count, len(hand)) or len(selected) != effect['_required'] or len(set(selected)) != len(selected):
            raise SemanticChoiceError('Connive requires exactly the offered number of distinct cards')
        discards = {}
        for ref in selected:
            retained = hand.get(ref)
            current = query.object(ref, zones=('hand',))
            if not isinstance(retained, Mapping) or set(retained) != {'logical_object_id', 'nonland'} or type(retained['nonland']) is not bool or current is None or current.owner != conniver.controller or current.logical_object_id != retained['logical_object_id']:
                raise SemanticChoiceError('Connive discard identity is unavailable or changed')
            discards[current.logical_object_id] = retained['nonland']
        return SemanticChoiceCompletion(
            intents=(MoveObjectsSimultaneouslyIntent(actor=conniver.controller, object_refs=selected, expected_zones=('hand',),
                      destination='graveyard', reason=effect['_stack_label'], transition_kind=ZoneTransitionKind.DISCARD, owned_only=True),),
            prepend_effects=(_phase(effect, 'counter', _discards=discards),),
        )
