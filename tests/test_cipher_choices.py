"""Pinned CR702.99 identity, physical-card and nontargeted choice boundaries."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorune.cipher_model import CipherEncoding, EncodeCipherIntent, cipher_candidates
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.object_query import ObjectQueryResult
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
from quorune.semantic_choices.cipher import CipherEncodingChoiceHandler
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_from_dict, ability_fragment_to_dict


class CipherChoiceTests(unittest.TestCase):
    def test_cipher_encoding_and_granted_fragment_roundtrip_preserve_both_incarnations(self):
        link = CipherEncoding(ContinuousObjectIdentity('card', 'card:exile:2'), ContinuousObjectIdentity('creature', 'creature:bf:1'), 'C1', 'C2')
        self.assertEqual(link, CipherEncoding.from_dict(link.to_dict()))
        for mutate in (lambda d: d.update(extra=None), lambda d: d.update(schema_version=True), lambda d: d.update(creature=d['card'])):
            raw = link.to_dict()
            mutate(raw)
            with self.assertRaises(ValueError):
                CipherEncoding.from_dict(raw)
        old = GrantedTriggeredAbilitySpec('ordinary', 'ordinary:key', 'creature.attacks.self', 'Ordinary')
        self.assertNotIn('cipher_card_ref', old.to_dict())
        self.assertEqual(old, ability_fragment_from_dict(ability_fragment_to_dict(old)))
        granted = GrantedTriggeredAbilitySpec('cipher:C1', 'builtin:cipher', 'damage.dealt.self', 'Cipher', 2, 'C1')
        self.assertEqual(granted, ability_fragment_from_dict(ability_fragment_to_dict(granted)))

    def test_cipher_encoding_offers_current_controlled_creatures_and_skips_copies(self):
        rows = tuple(ObjectQueryResult(object_id=ref, ref=ref, logical_object_id=ref + ':1', printed_name=ref,
            owner=owner, controller=controller, zone='battlefield', types=types, phased_out=phased)
            for ref, owner, controller, types, phased in (
                ('owned', 'A', 'A', ('creature',), False), ('borrowed', 'B', 'A', ('creature',), False),
                ('foreign', 'A', 'B', ('creature',), False), ('artifact', 'A', 'A', ('artifact',), False),
                ('phased', 'A', 'A', ('creature',), True)))
        self.assertEqual(('borrowed', 'owned'), tuple(row.ref for row in cipher_candidates(rows, 'A')))
        query = SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('A', 'B'), object_rows=rows,
                                            resolution_source_is_physical_card=True)
        context = SimpleNamespace(actor='A', stack_ref='S1', stack_label='Cipher spell', query=query,
                                  source_object_id='spell', source_logical_object_id='spell:stack:1')
        handler = CipherEncodingChoiceHandler()
        prepared = handler.prepare({'op': 'encode_cipher'}, context)
        self.assertEqual(('borrowed', 'owned'), prepared.request.choice.legal_refs)
        self.assertEqual(0, prepared.request.choice.minimum)
        continuation = SimpleNamespace(effect=prepared.continuation_effect)
        completed = handler.complete(continuation, {'cards': ['borrowed']}, query)
        self.assertIsInstance(completed.intents[0], EncodeCipherIntent)
        self.assertEqual('borrowed:1', completed.intents[0].creature.logical_object_id)
        for response in ({'cards': ['foreign']}, {'cards': ['owned', 'borrowed']}, {'cards': ['owned'], 'extra': 1}):
            with self.assertRaises(SemanticChoiceError):
                handler.complete(continuation, response, query)
        for physical in (False, None):
            copied_query = SnapshotSemanticChoiceQuery(seat_order=('A',), active_order=('A',), object_rows=rows,
                                                       resolution_source_is_physical_card=physical)
            context.query = copied_query
            self.assertIsNone(handler.prepare({'op': 'encode_cipher'}, context).request)

    def test_cipher_candidate_control_predicate_mutant_is_killed(self):
        row = ObjectQueryResult(object_id='foreign', ref='foreign', logical_object_id='foreign:1', printed_name='Foreign',
                                owner='B', controller='B', zone='battlefield', types=('creature',))
        def witness():
            self.assertEqual((), cipher_candidates((row,), 'A'))
        witness()
        with patch('test_cipher_choices.cipher_candidates', return_value=(row,)), self.assertRaises(AssertionError):
            witness()

    def test_cipher_link_reconciliation_is_incarnation_bound_and_preserves_queued_reference(self):
        from quorune.rules.cipher import current_cipher_encoding, reconcile_cipher_links
        link = CipherEncoding(ContinuousObjectIdentity('card', 'card:2'), ContinuousObjectIdentity('creature', 'creature:1'), 'C1', 'C2')
        card = SimpleNamespace(object_id='card', ref='C1', logical_object_id='card:2', zone='exile', annotations={'cipher_encoding': link.to_dict()})
        creature = SimpleNamespace(object_id='creature', ref='C2', logical_object_id='creature:1', zone='battlefield', annotations={})
        state = SimpleNamespace(cards={'card': card, 'creature': creature})
        self.assertEqual(link, current_cipher_encoding(state, card))
        creature.logical_object_id = 'creature:returned:2'
        self.assertIsNone(current_cipher_encoding(state, card))
        reconcile_cipher_links(SimpleNamespace(state=state))
        self.assertTrue(card.annotations['cipher_link_ended'])
        self.assertEqual(link, CipherEncoding.from_dict(card.annotations['cipher_encoding']))
        creature.logical_object_id = 'creature:1'
        self.assertIsNone(current_cipher_encoding(state, card))

    def test_cipher_intent_replacement_codec_and_version_two_dependency_are_structural(self):
        from quorune.cipher_model import cipher_intent_identity, validate_cipher_intent_identity
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity, validate_semantic_intent_identity, with_replacement_selections
        from quorune.rules.keyword_runtime_requirements import _fragment_descriptor_requirements
        intent = EncodeCipherIntent('A', ContinuousObjectIdentity('card', 'card:1'), ContinuousObjectIdentity('creature', 'creature:1'), 'C2', 'Cipher')
        identity = cipher_intent_identity(intent)
        self.assertEqual(identity, validate_cipher_intent_identity(identity))
        self.assertEqual(('cipher_encode', identity), semantic_intent_identity(intent))
        self.assertEqual(identity, validate_semantic_intent_identity('cipher_encode', identity))
        self.assertEqual(('replacement',), with_replacement_selections(intent, ('replacement',)).replacement_selections)
        fragment = ability_fragment_to_dict(GrantedTriggeredAbilitySpec('cipher:C1', 'builtin:cipher', 'damage.dealt.self', 'Cipher', 2, 'C1'))
        self.assertIn('casting.lifecycle.cipher', _fragment_descriptor_requirements(fragment))
