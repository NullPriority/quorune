"""Independent designation event facts, source binding and ownership boundaries."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.designation_event_model import (PermanentDesignationOccurrence, MONSTROUS_EVENT, RENOWNED_EVENT, DESIGNATION_EVENT_CAPABILITY)
from quorune.compiler.designation_event_bindings import designation_event_binding_spec
from quorune.rules.capabilities import load_default_capability_registry
from quorune.oracle_ir import compile_oracle_card
from test_qualified_zone_event_queries import query_record


class DesignationEventBindingTests(unittest.TestCase):
    def test_designation_occurrence_keeps_sealed_identity_and_strict_public_vocabulary(self):
        row = PermanentDesignationOccurrence('id', 'ref', 'id@1', 'B', ('creature',), ('gorgon',), 'monstrous', 0)
        self.assertEqual(MONSTROUS_EVENT, row.event)
        self.assertEqual(('ref', 'id@1', 0), tuple(row.context()[k] for k in ('card', 'logical_object_id', 'value')))
        self.assertEqual(RENOWNED_EVENT, replace(row, designation='renowned', value=None).event)
        for change in ({'value': True}, {'value': -1}, {'logical_object_id': ''}, {'types': ['creature']},
                       {'types': ('creature', 'artifact')}, {'subtypes': ('Gorgon',)}, {'designation': 'unknown'},
                       {'designation': 'renowned'}):
            with self.assertRaises(ValueError):
                replace(row, **change)

    def test_source_designation_and_public_renowned_heads_have_different_closed_predicates(self):
        own = designation_event_binding_spec('When this creature becomes monstrous, draw a card.')
        self.assertEqual(MONSTROUS_EVENT + '.self', own.event)
        self.assertEqual({'field': 'card', 'op': 'eq', 'value': '$source.ref'}, own.condition)
        public = designation_event_binding_spec('Whenever a creature you control becomes renowned, draw a card.')
        self.assertEqual(RENOWNED_EVENT, public.event)
        self.assertEqual({'all': [{'field': 'types', 'op': 'contains_any', 'value': ['creature']},
                                  {'field': 'controller', 'op': 'eq', 'value': '$source.controller'}]}, public.condition)
        for text in ('Whenever a creature you control becomes monstrous, draw a card.',
                     'Whenever a renowned creature enters, draw a card.',
                     'When this creature is monstrous, draw a card.',
                     'When this creature becomes monstrous or renowned, draw a card.'):
            self.assertIsNone(designation_event_binding_spec(text))

    def test_new_designation_compilation_keeps_actual_result_owners_and_old_enum_import(self):
        from quorune.compiler.fixed_counter_trigger_nodes import FixedCounterTriggerEvent as old
        from quorune.compiler.trigger_event_vocabulary import FixedCounterTriggerEvent as shared
        self.assertIs(old, shared)
        registry = load_default_capability_registry()
        for body in ('draw a card.', 'destroy target creature.', "create a token that's a copy of this creature."):
            record = replace(query_record('When this creature becomes monstrous, ' + body), type_line='Creature — Ooze')
            ir = compile_oracle_card(record, capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertIn(DESIGNATION_EVENT_CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
        self.assertNotEqual('exact', compile_oracle_card(query_record('When this creature becomes monstrous, rewrite history.'), capability_registry=registry).status)

    def test_designation_runtime_minimum_survives_missing_coverage_and_legacy_stream_is_distinct(self):
        from quorune.rules.designation_events import designation_event_runtime_dependencies
        from quorune.rules.event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
        for event, owner in ((MONSTROUS_EVENT, 'permanent.designation.monstrous'), (RENOWNED_EVENT, 'permanent.designation.renowned')):
            for primary, condition in ((event + '.self', None), ('permanent.enter', fixed_event_subscription_condition((FixedEventSubscription('permanent.enter'), FixedEventSubscription(event))))):
                p = SimpleNamespace(event=primary, event_condition=condition, coverage=())
                self.assertEqual({DESIGNATION_EVENT_CAPABILITY, owner}, set(designation_event_runtime_dependencies(p)))
        for event in ('permanent.becomes_monstrous', 'permanent.becomes_renowned', 'permanent.enter'):
            self.assertEqual((), designation_event_runtime_dependencies(SimpleNamespace(event=event, event_condition=None)))

    def test_designation_publisher_preserves_legacy_payload_and_broadcasts_typed_creature_facts_once(self):
        from quorune.permanent_designations import become_monstrous, BecomeMonstrousRequest
        from quorune.characteristic_evaluation import type_parts
        card = SimpleNamespace(object_id='id', ref='ref', logical_object_id='id@1', controller='B',
                               zone='battlefield', phased_out=False, monstrous_value=None)
        calls = []
        host = SimpleNamespace(state=SimpleNamespace(cards={'id': card}), _log=lambda *a, **kw: None,
                               _dispatch_semantic_event=lambda event, context, **kw: calls.append((event, context, kw)),
                               _effective_card_data=lambda c: {'type_line': 'Artifact Creature — Gorgon'}, _type_parts=type_parts)
        request = BecomeMonstrousRequest('id', 'ref', 'id@1', 0, 'A', 'test')
        result = become_monstrous(host, request)
        self.assertTrue(result.changed)
        self.assertEqual(['permanent.becomes_monstrous', MONSTROUS_EVENT], [c[0] for c in calls])
        self.assertEqual({'object': 'ref', 'logical_object_id': 'id@1', 'value': 0, 'controller': 'B', 'reason': 'test'}, calls[0][1])
        self.assertEqual({'sources': (card,)}, calls[0][2])
        self.assertEqual({}, calls[1][2])
        self.assertEqual(['artifact', 'creature'], calls[1][1]['types'])
        self.assertFalse(become_monstrous(host, request).changed)
        self.assertEqual(2, len(calls))
        card.monstrous_value = None
        card.logical_object_id = 'id@2'
        self.assertFalse(become_monstrous(host, request).changed)
        def witness():
            card.logical_object_id = 'id@1'
            calls.clear()
            become_monstrous(host, request)
            self.assertTrue(any(c[0] == MONSTROUS_EVENT for c in calls))
            card.monstrous_value = None
        witness()
        with patch('quorune.permanent_designations._publish_public_designation', return_value=None), self.assertRaises(AssertionError):
            witness()

    def test_verified_designation_owner_withdrawal_keeps_unrelated_entry_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        tests = set(discover_tests(ROOT).values())
        for owner in (DESIGNATION_EVENT_CAPABILITY, 'permanent.designation.monstrous'):
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id'] == owner).update(status='blocked', blockers=['designation owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual('exact', compile_oracle_card(query_record('When this creature becomes monstrous, draw a card.'), capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(query_record('When this creature enters, draw a card.'), capability_registry=registry).status)
