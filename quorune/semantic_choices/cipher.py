"""Optional nontargeted encoding after a physical Cipher spell's other effects."""
from dataclasses import dataclass
from ..cipher_model import CIPHER_CAPABILITY, CIPHER_ENCODE_OPERATION, EncodeCipherIntent, cipher_candidates
from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap, thaw_value
from .model import AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest


@dataclass(frozen=True, slots=True)
class CipherEncodingChoiceHandler:
    operation: str = CIPHER_ENCODE_OPERATION
    handler_id: str = 'choice.public.cipher-encode.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 702.99a', 'CR 702.99b', 'CR 702.99c')
    capability_dependencies: tuple[str, ...] = (CIPHER_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ('_cipher_card', '_cipher_actor', '_cipher_offered', '_cipher_reason')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('EncodeCipherIntent', 'rules.cipher.encode_cipher')
    replay_fixture: str = 'cipher-encode'
    test_modules: tuple[str, ...] = ('tests.test_cipher_choices',)

    def prepare(self, effect, context):
        if set(effect) != {'op'} or effect['op'] != self.operation:
            raise SemanticChoiceError('Cipher encoding instruction has a closed schema')
        if context.query.resolution_source_is_physical_card is not True:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='Only a physical card can be encoded'))
        try:
            card = ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)
        except ValueError as exc:
            raise SemanticChoiceError('Cipher spell lost its original incarnation') from exc
        candidates = cipher_candidates(context.query.objects(zones=('battlefield',)), context.actor)
        stored = FrozenMap({'op': self.operation, '_cipher_card': card.to_dict(), '_cipher_actor': context.actor,
            '_cipher_reason': context.stack_label, '_cipher_offered': {row.ref: {'object_id': row.object_id,
                'logical_object_id': row.logical_object_id} for row in candidates}})
        if not candidates:
            return SemanticChoicePreparation(request=None, continuation_effect=stored,
                auto_continue=AutoContinue(reason='No controlled creature can receive Cipher'))
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt='Exile this spell card encoded on a creature you control, or decline.',
            choice=ObjectChoice(field_name='cards', legal_refs=tuple(row.ref for row in candidates),
                zones=('battlefield',), minimum=0, maximum=1, optional=True, visibility='public', controller_relation='actor'),
            public_context=FrozenMap({'operation': self.operation, 'stack': context.stack_ref})), continuation_effect=stored)

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != {'op', '_cipher_card', '_cipher_actor', '_cipher_reason', '_cipher_offered'} or effect['op'] != self.operation:
            raise SemanticChoiceError('Cipher completion lost its retained identity')
        if set(response) - {'cards', 'action_id', 'action', 'choice_schema'}:
            raise SemanticChoiceError('Cipher completion accepts only creature references')
        refs = response.get('cards')
        if not isinstance(refs, (list, tuple)) or len(refs) > 1 or any(type(ref) is not str for ref in refs):
            raise SemanticChoiceError('Cipher chooses at most one offered creature')
        actor = effect['_cipher_actor']
        card = ContinuousObjectIdentity.from_dict(thaw_value(effect['_cipher_card']))
        selected = None
        if refs:
            raw = effect['_cipher_offered'].get(refs[0])
            if raw is None:
                raise SemanticChoiceError('Cipher selected creature was not offered')
            selected = ContinuousObjectIdentity.from_dict(thaw_value(raw))
            if not any(row.ref == refs[0] and row.object_id == selected.object_id and row.logical_object_id == selected.logical_object_id
                       for row in cipher_candidates(query.objects(zones=('battlefield',)), actor)):
                raise SemanticChoiceError('Cipher selected creature is no longer eligible')
        return SemanticChoiceCompletion(intents=(EncodeCipherIntent(actor, card, selected, refs[0] if refs else None, effect['_cipher_reason']),))
