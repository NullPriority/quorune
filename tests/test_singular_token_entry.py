"""CR 603.2c: singular token entry does not coalesce a simultaneous batch."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.compiler.fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


class SingularTokenEntryTests(unittest.TestCase):
    def test_singular_and_batch_token_entry_keep_identical_predicates_but_distinct_coalescing(self):
        registry = load_default_capability_registry()
        conditions = []
        for subject, verb, batch in (('a token', 'enters', False), ('one or more tokens', 'enter', True)):
            text = f'Whenever {subject} you control {verb}, you draw a card and lose 1 life.'
            binding = fixed_public_action_event_binding_spec(text)
            self.assertEqual('permanent.enter', binding.event)
            conditions.append(binding.condition)
            ir = compile_oracle_card(query_record(text), capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertEqual(batch, 'one_or_more_event_batch' in node.runtime_coverage)
            self.assertEqual(['draw', 'lose_life'], [effect['op'] for effect in node.effects])
            self.assertIn('trigger.event.normalized_zone_change', node.capability_dependencies)
        self.assertEqual(conditions[0], conditions[1])
        self.assertEqual({'all': [{'field': 'controller', 'op': 'eq', 'value': '$source.controller'},
                                 {'field': 'token', 'op': 'truthy', 'value': True}]}, conditions[0])

    def test_singular_token_entry_retains_unrepresented_qualifiers_and_unknown_results(self):
        registry = load_default_capability_registry()
        for text in ('Whenever a token you control enter, draw a card.',
                     'Whenever one or more tokens you control enters, draw a card.',
                     'Whenever a token you control enters for the first time each turn, draw a card.',
                     'Whenever a token you own enters, draw a card.',
                     'Whenever a token you control enters, draw a card. Rewrite history.'):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=registry).status, text)

    def test_singular_token_entry_verified_zone_owner_withdrawal_preserves_unrelated_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        next(row for row in raw['capabilities'] if row['id'] == 'trigger.event.normalized_zone_change').update(status='blocked', blockers=['token entry occurrence authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', compile_oracle_card(query_record('Whenever a token you control enters, draw a card.'), capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(replace(query_record('Draw a card.'), type_line='Sorcery'), capability_registry=registry).status)

    def test_singular_token_entry_batch_variant_mutant_is_killed(self):
        import quorune.compiler.fixed_public_action_event_bindings as owner
        original = owner._public_token_zone_spec
        text = 'Whenever a token you control enters, draw a card.'
        def witness():
            ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
            self.assertEqual('exact', ir.status)
            self.assertNotIn('one_or_more_event_batch', ir.faces[0].nodes[0].runtime_coverage)
        witness()
        def batched(material):
            binding = original(material)
            return replace(binding, variant='one_or_more_controlled_tokens_enter') if binding is not None and binding.variant == 'controlled_token_enters' else binding
        with patch.object(owner, '_public_token_zone_spec', side_effect=batched), self.assertRaises(AssertionError):
            witness()
