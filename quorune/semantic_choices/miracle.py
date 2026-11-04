from __future__ import annotations

"""Public cast-or-decline choice for a resolving intrinsic Miracle trigger."""

from dataclasses import dataclass
from collections.abc import Mapping
import hashlib

from ..drawing.miracle_model import (
    FixedMiracleCost, MiracleRevealedHand, MIRACLE_CAPABILITY_ID, MIRACLE_CHOICE_OPERATION,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.context import SemanticSourceContext
from ..semantic_runtime.miracle_intent import MiracleCastChoiceIntent
from ..util import stable_json
from .model import (
    CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


def _fingerprint(options):
    return hashlib.sha256(stable_json([thaw_value(option) for option in options]).encode()).hexdigest()


def _hand(effect):
    try:
        hand = MiracleRevealedHand.from_dict(effect.get('hand'))
        cost = FixedMiracleCost.from_dict(effect.get('cost'))
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if hand.cost != cost:
        raise SemanticChoiceError('Miracle cost differs from its revealed card')
    return hand


@dataclass(frozen=True, slots=True)
class MiracleCastChoiceHandler:
    operation: str = MIRACLE_CHOICE_OPERATION
    handler_id: str = 'choice.miracle.cast-or-decline.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 109.5', 'CR 400.7', 'CR 603.11', 'CR 702.94a', 'CR 702.94b', 'CR 707.10')
    capability_dependencies: tuple[str, ...] = (MIRACLE_CAPABILITY_ID,)
    continuation_fields: tuple[str, ...] = ('cost','hand','_choice_actor','_trigger_ref','_options_fingerprint')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt','card','cast_options','legal_actions.choice_schema.legal_values',
        'legal_actions.choice_schema.cast_options','legal_actions.choice_schema.cast_options_when')
    mutation_path: tuple[str, ...] = ('MiracleCastChoiceIntent','drawing.miracle_casting.execute_miracle_cast_choice')
    replay_fixture: str = 'miracle-fixed-mana-cast-choice'
    test_modules: tuple[str, ...] = ('tests.test_miracle_rules',)

    def prepare(self, effect, context):
        if set(effect) != {'op','cost','hand'} or effect['op'] != self.operation:
            raise SemanticChoiceError('Miracle choice effect has a closed schema')
        hand = _hand(effect)
        if context.actor != context.stack_controller:
            raise SemanticChoiceError('Miracle choice requires the current trigger controller')
        options = tuple(context.query.authorized_cast_options())
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt='Cast the revealed card for its Miracle cost?',
                choice=CastOrDeclineChoice(options),
                public_context=FrozenMap({'stack':context.stack_ref,'operation':self.operation,
                                          'card':hand.hand_ref,'cast_options':list(options)}),
            ),
            continuation_effect=FrozenMap({**effect,'_choice_actor':context.actor,
                '_trigger_ref':context.stack_ref,'_options_fingerprint':_fingerprint(options)}),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response, query):
        effect = continuation.effect
        if (set(effect) != {'op','cost','hand','_choice_actor','_trigger_ref','_options_fingerprint'}
                or effect.get('op') != self.operation or effect['_trigger_ref'] != continuation.stack_ref):
            raise SemanticChoiceError('Miracle choice continuation changed')
        hand = _hand(effect)
        options = tuple(query.authorized_cast_options())
        if _fingerprint(options) != effect.get('_options_fingerprint'):
            raise SemanticChoiceError('Miracle cast options changed')
        choice = response.get('choice')
        if choice not in ({'cast','decline'} if options else {'decline'}):
            raise SemanticChoiceError('Choose a current Miracle action')
        raw_cast = response.get('cast')
        if raw_cast is not None and not isinstance(raw_cast, Mapping):
            raise SemanticChoiceError('Miracle cast submission must be an object')
        cast_response = dict(raw_cast or {})
        cast_response.update({key:value for key,value in response.items() if key not in {'action','cast','choice'}})
        return SemanticChoiceCompletion(intents=(MiracleCastChoiceIntent(
            actor=str(effect['_choice_actor']),
            source=SemanticSourceContext(stack_ref=str(effect['_trigger_ref']),
                object_id=hand.card_object_id,logical_object_id=hand.hand_logical_object_id,card_ref=hand.hand_ref),
            hand=hand,choice=choice,options_fingerprint=str(effect['_options_fingerprint']),
            response=FrozenMap(cast_response),
        ),))
