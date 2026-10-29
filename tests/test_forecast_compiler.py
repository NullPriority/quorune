"""Forecast lowers through the existing activated catalog with explicit rules."""
from dataclasses import replace
from copy import deepcopy
import json
import unittest
from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


class ForecastCompilerTests(unittest.TestCase):
    def test_forecast_source_span_hand_catalog_and_intrinsic_restrictions_are_closed(self):
        text = 'Forecast — {2}{U}, Reveal this card from your hand: Draw a card.'
        record = replace(query_record(text), keywords=('Forecast',), type_line='Creature — Wizard', power='2', toughness='2')
        ir = compile_oracle_card(record, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = next(n for n in ir.faces[0].nodes if n.event == 'activate')
        self.assertEqual('hand', node.active_zone)
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertIn('activation.forecast.hand', node.capability_dependencies)
        from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
        ability = compile_activated_ability_catalog(record)['front'][0].to_dict()
        self.assertIs(ability['forecast'], True)
        self.assertEqual('once_per_turn', ability['activation_limit'])
        self.assertTrue(any(c['kind'] == 'controllers_upkeep' for c in ability['activation_conditions']))
        bad = replace(record, oracle_text='Forecast — {2}{U}: Draw a card.')
        self.assertNotEqual('exact', compile_oracle_card(bad, capability_registry=load_default_capability_registry()).status)

    def test_forecast_unknown_bodies_and_missing_reveal_do_not_promote_card(self):
        for text in ('Forecast — {U}, Reveal this card from your hand: Invent an unknown result.',
                     'Forecast — {U}: Draw a card.', 'Forecast — {U}, Reveal another card from your hand: Draw a card.'):
            record = replace(query_record(text), keywords=('Forecast',), type_line='Creature — Wizard', power='2', toughness='2')
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=load_default_capability_registry()).status, text)

    def test_forecast_verified_owner_withdrawal_keeps_ordinary_draw_activation(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json');tests = set(discover_tests(ROOT).values())
        record = replace(query_record('Forecast — {U}, Reveal this card from your hand: Draw a card.'), keywords=('Forecast',), type_line='Creature — Wizard', power='2', toughness='2')
        for cap in ('activation.forecast.hand', 'activation.usage.once_per_turn', 'activation.condition.phase_window'):
            changed = deepcopy(raw);next(r for r in changed['capabilities'] if r['id'] == cap).update(status='blocked', blockers=['Forecast owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=tests)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(replace(record, oracle_text='{U}: Draw a card.', keywords=()), capability_registry=registry).status)
