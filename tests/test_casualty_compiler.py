"""CR702.153/601.2/707.10: one paid creature threshold, one independent copy."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.casualty_model import FixedCasualtySpec, validate_casualty_fact, CASUALTY_CAPABILITY
from quorune.power_sacrifice_model import power_sacrifice_predicate, POWER_SACRIFICE_CAPABILITY
from quorune.object_query import ObjectQueryResult, query_objects
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.casualty_costs import casualty_sacrifice_cost
from test_qualified_zone_event_queries import query_record


class CasualtyModelCompilerTests(unittest.TestCase):
    def test_casualty_spec_and_payment_fact_seal_one_distinct_instance_and_incarnation(self):
        spec = FixedCasualtySpec('front:casualty:line:1', 2)
        self.assertEqual(spec, FixedCasualtySpec.from_dict(spec.to_dict()))
        row = {'ref': 'sacrifice', 'logical_object_id': 'body@1'}
        fact = spec.payment_fact(row);row['ref'] = 'changed'
        self.assertEqual('sacrifice', fact['object']['ref'])
        for change in ({'schema_version': True}, {'minimum_power': True}, {'minimum_power': -1}, {'instance_id': ''}, {'extra': 0}):
            with self.assertRaises(ValueError):FixedCasualtySpec.from_dict({**spec.to_dict(), **change})
        for change in ({'object': []}, {'object': {'ref': 'sacrifice'}}, {'schema_version': True}, {'extra': 0}):
            with self.assertRaises(ValueError):validate_casualty_fact({**fact, **change})

    def test_casualty_power_cost_uses_current_creature_power_and_control_without_tap_price(self):
        def row(ref, power, **changes):
            return replace(ObjectQueryResult(ref, ref, ref, 'A', 'A', 'battlefield', types=('creature',), effective_power=power, effective_toughness=9), **changes)
        rows = (row('equal', 2, tapped=True), row('above', 3), row('below', 1), row('unknown', None),
            row('foreign', 10, owner='B', controller='B'), row('artifact', 9, types=('artifact',)),
            row('phased', 9, phased_out=True), row('token', 2, token=True), row('borrowed', 2, owner='B'))
        spec = FixedCasualtySpec('fixed', 2)
        def assert_boundary():
            from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost
            from quorune.rules.casting_additional_cost_groups import selected_zone_cost_payload_dependencies
            cost = casualty_sacrifice_cost(spec)
            self.assertEqual(cost, FixedZoneChangeAdditionalCost.from_descriptor(cost.to_descriptor()))
            self.assertEqual({'equal', 'above', 'token', 'borrowed'}, {r.ref for r in query_objects(rows, cost.bound_predicate('A'))})
            self.assertIn(POWER_SACRIFICE_CAPABILITY, selected_zone_cost_payload_dependencies({'additional_costs': [cost.to_descriptor()]}))
        assert_boundary()
        with patch('quorune.rules.casting.casualty_costs.power_sacrifice_predicate', return_value=power_sacrifice_predicate(0)):
            with self.assertRaises(AssertionError):assert_boundary()

    def test_casualty_numeric_cost_version_and_payload_reject_unrepresented_variants(self):
        from quorune.rules.casting_additional_costs import FixedZoneChangeAdditionalCost, AdditionalCostError
        from quorune.target_numeric import TargetNumericCharacteristic, TargetNumericComparison, TargetNumericCharacteristicSpec
        cost = casualty_sacrifice_cost(FixedCasualtySpec('fixed', 2))
        for changes in ({'operation': 'discard_one', 'choice_field': 'discard_cards'}, {'count': 2},
                {'predicate': replace(cost.predicate, types_all=('artifact',), numeric_characteristic=None)},
                {'predicate': replace(cost.predicate, numeric_characteristic=TargetNumericCharacteristicSpec(TargetNumericCharacteristic.TOUGHNESS, TargetNumericComparison.AT_LEAST, 2))}):
            with self.assertRaises(AdditionalCostError):replace(cost, **changes)
        plain = replace(cost, schema_version=1, predicate=replace(cost.predicate, numeric_characteristic=None))
        self.assertEqual(plain, FixedZoneChangeAdditionalCost.from_descriptor(plain.to_descriptor()))
        legacy_numeric = replace(cost, schema_version=1)
        self.assertEqual(legacy_numeric, FixedZoneChangeAdditionalCost.from_descriptor(legacy_numeric.to_descriptor()))

    def test_casualty_source_span_and_complete_companion_require_independent_owners(self):
        from quorune.semantic_runtime.casualty import FixedCasualtyHandler
        record = replace(query_record('Casualty 2\nDraw a card.'), type_line='Sorcery', keywords=('Casualty',))
        ir = compile_oracle_card(record, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = next(n for n in ir.faces[0].nodes if n.event == 'cast.cost')
        self.assertEqual((0, 10), (node.span.start, node.span.end))
        self.assertIn(CASUALTY_CAPABILITY, node.capability_dependencies)
        handler = FixedCasualtyHandler()
        self.assertEqual(2, handler.validate(node.handlers[0]).minimum_power)
        for change in ({'schema_version': True}, {'requires_complete_card_program': False}, {'extra': 0}):
            with self.assertRaises(ValueError):handler.validate({**node.handlers[0], **change})
        for text in ('Casualty X\nDraw a card.', 'Casualty 1\nCasualty 2\nDraw a card.',
                     'Casualty 1\nInvent an unknown result.', 'Casualty 2\nConspire\nDraw a card.'):
            self.assertNotEqual('exact', compile_oracle_card(replace(record, oracle_text=text), capability_registry=load_default_capability_registry()).status)
        self.assertNotEqual('exact', compile_oracle_card(replace(record, type_line='Creature — Wizard', power='2', toughness='2'), capability_registry=load_default_capability_registry()).status)

    def test_casualty_verified_capability_withdrawal_rejects_source_and_keeps_plain_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        discovered = set(discover_tests(ROOT).values());decl = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        record = replace(query_record('Casualty 2\nDraw a card.'), type_line='Sorcery', keywords=('Casualty',))
        for capability in (CASUALTY_CAPABILITY, POWER_SACRIFICE_CAPABILITY, 'stack.copy.fixed_instant_or_sorcery'):
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id'] == capability).update(status='blocked', blockers=['Casualty authority withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=decl, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(replace(record, oracle_text='Draw a card.', keywords=()), capability_registry=registry).status)
