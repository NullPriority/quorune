"""Optional untargeted Soulbond choice over immutable authorized candidates."""
from __future__ import annotations

from dataclasses import dataclass
from ..replacement.immutable import FrozenMap
from ..soulbond_model import SoulbondOpportunity, SOULBOND_CAPABILITY, SOULBOND_CHOICE_OPERATION
from ..semantic_runtime.soulbond_intent import PairSoulbondIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _opportunity(effect, actor, active_seats):
    try:
        opportunity = SoulbondOpportunity.from_effect(effect)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if opportunity.controller != actor or actor not in active_seats:
        raise SemanticChoiceError('Soulbond choice belongs to its active trigger controller')
    return opportunity


@dataclass(frozen=True, slots=True)
class SoulbondPairChoiceHandler:
    operation: str = SOULBOND_CHOICE_OPERATION
    handler_id: str = 'choice.soulbond.pair.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 702.95a','CR 702.95c','CR 702.95d','CR 603.4','CR 400.7')
    capability_dependencies: tuple[str, ...] = (SOULBOND_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ('player','source_ref','source_identity','mode','entered_ref','entered_identity')
    private_data: tuple[str, ...] = ('source_identity','entered_identity')
    projected_fields: tuple[str, ...] = ('prompt','legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('PairSoulbondIntent','soulbond_state.commit_soulbond_pair')
    replay_fixture: str = 'soulbond-pairing'
    test_modules: tuple[str, ...] = ('tests.test_soulbond',)

    def prepare(self, effect, context):
        opportunity = _opportunity(effect, context.actor, context.query.active_seats)
        refs = context.query.choice_candidate_refs()
        if not refs:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(opportunity.to_effect()),
                auto_continue=AutoContinue('Soulbond has no eligible unpaired creature'))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(prompt='Pair with another creature?',
                choice=ObjectChoice(field_name='cards', legal_refs=refs, zones=('battlefield',),
                    minimum=0, maximum=1, optional=True, controller_relation='actor'),
                public_context=FrozenMap({'stack':context.stack_ref, 'operation':self.operation,
                                          'source':opportunity.source_ref})),
            continuation_effect=FrozenMap(opportunity.to_effect()))

    def complete(self, continuation, response, query):
        opportunity = _opportunity(continuation.effect, continuation.effect.get('player'), query.active_seats)
        cards = response.get('cards')
        if type(cards) is not list or len(cards) > 1 or any(type(ref) is not str for ref in cards):
            raise SemanticChoiceError('Choose at most one Soulbond partner')
        if not cards:
            return SemanticChoiceCompletion()
        if cards[0] not in query.choice_candidate_refs():
            raise SemanticChoiceError('The chosen Soulbond partner is no longer eligible')
        partner = query.object(cards[0], zones=('battlefield',))
        if partner is None or not partner.logical_object_id:
            raise SemanticChoiceError('The chosen Soulbond incarnation is unavailable')
        return SemanticChoiceCompletion(intents=(PairSoulbondIntent(
            actor=opportunity.controller, source_ref=opportunity.source_ref,
            source_identity=opportunity.source_identity, partner_ref=partner.ref,
            partner_identity=partner.logical_object_id, reason='Soulbond',
            pair_id=f'soulbond:{continuation.stack_ref}:{opportunity.source_identity}:{partner.logical_object_id}',
            entered_ref=opportunity.entered_ref, entered_identity=opportunity.entered_identity),))
