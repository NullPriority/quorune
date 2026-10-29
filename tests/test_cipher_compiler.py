"""Cipher promotes only a complete ordinary companion spell body."""
from dataclasses import replace
import unittest
from test_qualified_zone_event_queries import query_record
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


class CipherCompilerTests(unittest.TestCase):
    def test_cipher_unknown_keyword_shapes_and_unsupported_companions_remain_residual(self):
        registry = load_default_capability_registry()
        for text in ('Draw a card.\nCipher 2', 'Draw a card.\nCipher\nCipher', 'Draw a card.\nCipher\nRewrite history.', 'Rewrite history.\nCipher'):
            card = replace(query_record(text), type_line='Sorcery', keywords=('Cipher',))
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=registry).status, text)

    def test_cipher_verified_owner_withdrawal_blocks_whole_source_and_keeps_plain_draw(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        next(row for row in raw['capabilities'] if row['id'] == 'casting.lifecycle.cipher').update(status='blocked', blockers=['Cipher authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        card = replace(query_record('Draw a card.\nCipher'), type_line='Sorcery', keywords=('Cipher',))
        self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(replace(card, oracle_text='Draw a card.', keywords=()), capability_registry=registry).status)

    def test_cipher_appends_encoding_after_body_and_declares_exile_grant(self):
        card = replace(query_record('Draw a card.\nCipher'), type_line='Sorcery', keywords=('Cipher',))
        ir = compile_oracle_card(card, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        spell = next(n for n in ir.faces[0].nodes if n.kind == 'spell_ability')
        self.assertEqual(['draw', 'encode_cipher'], [effect['op'] for effect in spell.effects])
        self.assertTrue(any(n.active_zone == 'exile' and n.event == 'characteristics.evaluate' for n in ir.faces[0].nodes))
        bad = replace(card, oracle_text='Rewrite history.\nCipher')
        self.assertNotEqual('exact', compile_oracle_card(bad, capability_registry=load_default_capability_registry()).status)
