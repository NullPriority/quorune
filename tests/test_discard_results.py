"""Pinned CR701.9/608.2/121.2: follow committed counts, not hand guesses."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorune.discard_result_model import (DiscardWholeHandsResultIntent, IntentResultPlayerDraws,
    PlayerDiscardCounts, decode_discard_result_identity)
from quorune.replacement.immutable import FrozenMap
from quorune.semantic_choices.model import AutoContinue, SemanticChoiceError
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


class DiscardResultTests(unittest.TestCase):
    def test_discard_result_counts_bind_own_and_greatest_with_empty_and_plus_one(self):
        result = PlayerDiscardCounts((('B', 3), ('C', 0), ('D', 1), ('A', 2)))
        for reduction, offset, expected in (('own', 0, [3, 0, 1, 2]), ('greatest', 0, [3] * 4), ('own', 1, [4, 1, 2, 3])):
            effects = AutoContinue('Committed results', player_draws_from_result=IntentResultPlayerDraws(0, reduction, offset)).resolved_effects({0: result})
            self.assertEqual(['B', 'C', 'D', 'A'], [e['player'] for e in effects])
            self.assertEqual(expected, [e['count'] for e in effects])
        with self.assertRaises(SemanticChoiceError):AutoContinue('Missing', player_draws_from_result=IntentResultPlayerDraws(0, 'own')).resolved_effects({})
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.compiler.discard_result_templates import discard_result_draw_template
        effect = discard_result_draw_template('Discard your hand, then draw that many cards.')[1][0]
        self.assertEqual(('zone.discard.whole_hand_result',), typed_result_amount_runtime_dependencies(effect))
        with self.assertRaises(ValueError):typed_result_amount_runtime_dependencies({**effect, 'offset': 2})
        for counts in ((('A', True),), (('A', -1),), (('A', 1), ('A', 0))):
            with self.assertRaises(ValueError):PlayerDiscardCounts(counts)

    def test_discard_result_identity_seals_private_members_and_rejects_malformed_data(self):
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity, validate_semantic_intent_identity, with_replacement_selections
        intent = DiscardWholeHandsResultIntent('A', ('B',), FrozenMap({'B': [{'object_id': 'b', 'logical_object_id': 'b@1', 'ref': 'private-b'}]}), 'Discard')
        kind, data = semantic_intent_identity(intent)
        self.assertEqual(data, validate_semantic_intent_identity(kind, data))
        self.assertEqual(data, semantic_intent_identity(with_replacement_selections(intent, ('redirect',)))[1])
        for change in ({'players': ['A']}, {'extra': 0}, {'hands': {'B': [{'ref': 'private-b'}]}}):
            with self.assertRaises(ValueError):decode_discard_result_identity({**data, **change})
        from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
        from quorune.semantic_choices.discard_results import DiscardResultDrawHandler
        effect = {'op': 'discard_hands_then_draw', 'schema_version': 1, 'actor': 'A', 'players': ['B'], 'reduction': 'own', 'offset': 0}
        query = SnapshotSemanticChoiceQuery(tuple('AB'), tuple('AB'), apnap_order=tuple('BA'))
        context = SemanticChoiceContext('A', 'stack', 'A', 'Discard', None, None, 'discard-program', 1, query)
        handler = DiscardResultDrawHandler()
        with self.assertRaises(SemanticChoiceError):handler.prepare(effect, context)
        query = replace(query, discard_hand_inputs=intent.hands)
        prepared = handler.prepare(effect, replace(context, query=query))
        self.assertEqual(intent.hands, prepared.preparation_intents[0].hands)
        self.assertEqual((), query.objects(zones=('hand',)))
        for bad_query in (replace(query, apnap_order=('B', 'B', 'A')), replace(query, apnap_order=())):
            with self.assertRaises(SemanticChoiceError):handler.prepare(effect, replace(context, query=bad_query))

    def test_discard_result_owner_uses_only_new_committed_selected_incarnations(self):
        from quorune.rules.discard_results import execute_discard_result
        from quorune.model import TurnHistoryEvent
        from quorune.errors import GameRuleError
        member = {'object_id': 'b', 'logical_object_id': 'b@1', 'ref': 'private-b'}
        snapshot = {'hands': [{'seat': 'B', 'members': [member]}]}
        intent = DiscardWholeHandsResultIntent('A', ('B',), FrozenMap({'B': [member]}), 'Discard')
        old = TurnHistoryEvent('card_discarded', actor='B', object_incarnation='old@1')
        unrelated = TurnHistoryEvent('card_discarded', actor='B', object_incarnation='other@1')
        committed = TurnHistoryEvent('card_discarded', actor='B', object_incarnation='b@1')
        def witness(new_events):
            events = [old]
            host = SimpleNamespace(_current_turn_history=lambda kind: tuple(events))
            with patch('quorune.rules.discard_results.whole_hand_input_snapshot', return_value=snapshot), patch('quorune.semantic_runtime.whole_hand_discard.execute_whole_hand_discard', side_effect=lambda *args: events.extend(new_events)):
                return execute_discard_result(host, intent)
        self.assertEqual((('B', 1),), witness([unrelated, committed]).counts)
        self.assertEqual((('B', 0),), witness([unrelated]).counts)
        with patch('quorune.rules.discard_results.whole_hand_input_snapshot', return_value={'hands': [{'seat': 'B', 'members': []}]}):
            with self.assertRaises(GameRuleError):execute_discard_result(SimpleNamespace(), intent)
        # The same independent witness rejects an implementation guessing hand size.
        with patch('quorune.rules.discard_results.PlayerDiscardCounts', return_value=PlayerDiscardCounts((('B', 1),))):
            with self.assertRaises(AssertionError):self.assertEqual((('B', 0),), witness([]).counts)


class DiscardResultCompilerTests(unittest.TestCase):
    def test_discard_result_compiler_owns_complete_linked_counts_and_source_spans(self):
        for text in ('Discard all the cards in your hand, then draw that many cards.',
                     'Discard your hand, then draw that many cards plus one.',
                     'Each player discards their hand, then draws that many cards.',
                     'Each player discards their hand, then draws cards equal to the greatest number of cards a player discarded this way.'):
            ir = compile_oracle_card(replace(query_record(text), type_line='Sorcery'), capability_registry=load_default_capability_registry())
            self.assertEqual('exact', ir.status, (text, ir.material_residuals))
            node = ir.faces[0].nodes[0]
            self.assertEqual('discard_hands_then_draw', node.effects[0]['op'])
            self.assertEqual((0, len(text)), (node.span.start, node.span.end))
            self.assertIn('zone.discard.whole_hand_result', node.capability_dependencies)

    def test_discard_result_compiler_rejects_filtered_unknown_and_unbound_counts(self):
        from quorune.compiler.discard_result_templates import discard_result_draw_template
        for text in ('Discard any number of cards, then draw that many cards.', 'Discard your hand, then draw that many cards plus two.',
                     'Each player on your team discards their hand, then draws that many cards.',
                     'That player discards their hand, then draws that many cards.',
                     'Discard your hand, then draw cards equal to the greatest number of cards a player discarded this way.',
                     'Discard your hand, then draw that many cards. Rewrite history.'):
            self.assertIsNone(discard_result_draw_template(text), text)
        from quorune.discard_result_model import validate_discard_draw
        base = discard_result_draw_template('Discard your hand, then draw that many cards.')[1][0]
        for change in ({'offset': True}, {'reduction': 'sum'}, {'schema_version': True}, {'players': ['A', 'B']}, {'unknown': 0}):
            with self.assertRaises(ValueError):validate_discard_draw({**base, **change})

    def test_discard_result_verified_owner_withdrawal_keeps_unrelated_draw(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == 'zone.discard.whole_hand_result').update(status='blocked', blockers=['Result authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        card = replace(query_record('Discard your hand, then draw that many cards.'), type_line='Sorcery')
        self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(replace(card, oracle_text='Draw a card.'), capability_registry=registry).status)
