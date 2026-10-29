"""Bargain declarations, entry links and same-target branches close independently."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.bargain_model import BARGAIN_INSTANCE


class BargainCompilerTests(unittest.TestCase):
    def test_bargain_entry_and_same_target_branch_preserve_complete_source_and_instance(self):
        registry = load_default_capability_registry()
        text = 'Bargain\nWhen this creature enters, if it was bargained, each opponent loses 3 life and you gain 3 life.'
        record = replace(query_record(text), keywords=('Bargain',), type_line='Creature — Faerie', power='3', toughness='3')
        ir = compile_oracle_card(record, capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        trigger = next(n for n in ir.faces[0].nodes if n.event == 'permanent.enter.self')
        self.assertEqual({'field': 'declared_cast_cost_instances', 'op': 'contains_any', 'value': [BARGAIN_INSTANCE]}, trigger.event_condition)
        self.assertEqual(text.index('When'), trigger.span.start)
        token_text = 'Bargain\nWhen this creature enters, if it was bargained, create a 1/1 white Soldier creature token.'
        token_ir = compile_oracle_card(replace(record, oracle_text=token_text), capability_registry=registry)
        self.assertEqual('exact', token_ir.status, token_ir.material_residuals)
        text = 'Bargain\nTarget creature gets -3/-3 until end of turn. If this spell was bargained, that creature gets -5/-5 until end of turn instead.'
        ir = compile_oracle_card(replace(record, oracle_text=text, type_line='Instant', power=None, toughness=None), capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        spell = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        self.assertEqual(12, spell.effects[0]['schema_version'])
        self.assertEqual(BARGAIN_INSTANCE, spell.effects[0]['condition']['instance_id'])
        self.assertEqual(1, spell.target_schema['count'])

    def test_bargain_grammar_unknown_siblings_unlinked_and_conditional_targets_remain_residual(self):
        record = replace(query_record('Bargain\nDraw a card.'), keywords=('Bargain',), type_line='Sorcery')
        for text in ('Bargain 2\nDraw a card.', 'Bargain\nBargain\nDraw a card.', 'Bargain\nInvent an unknown result.',
                     'Bargain\nCasualty 2\nDraw a card.', 'If this spell was bargained, target land becomes a creature.'):
            self.assertNotEqual('exact', compile_oracle_card(replace(record, oracle_text=text), capability_registry=load_default_capability_registry()).status, text)

    def test_bargain_verified_owner_withdrawal_blocks_linked_program_and_keeps_plain_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        decl = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json');discovered = set(discover_tests(ROOT).values())
        record = replace(query_record('Bargain\nDraw a card.'), keywords=('Bargain',), type_line='Sorcery')
        for cap in ('casting.additional_cost.bargain', 'casting.additional_cost.permanent_union', 'casting.additional_cost.declared_instance'):
            changed = deepcopy(raw);next(r for r in changed['capabilities'] if r['id'] == cap).update(status='blocked', blockers=['Bargain owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=decl, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(replace(record, oracle_text='Draw a card.', keywords=()), capability_registry=registry).status)
