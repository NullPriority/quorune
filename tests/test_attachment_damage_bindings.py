"""CR 120.4b/303.4e: each attached damage qualifier is independent."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
from common import ROOT
from test_qualified_zone_event_queries import query_record
from quorune.compiler.fixed_public_event_trigger_bindings import fixed_public_event_binding_spec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


class AttachmentDamageBindingTests(unittest.TestCase):
    def test_attached_damage_relation_combat_and_recipient_matrix_preserves_complete_results(self):
        registry = load_default_capability_registry()
        for relation in ('enchanted', 'equipped'):
            for combat in ('', 'combat '):
                for recipient in ('', ' to a player', ' to an opponent'):
                    text = f'Whenever {relation} creature deals {combat}damage{recipient}, you draw a card and lose 1 life.'
                    with self.subTest(text=text):
                        binding = fixed_public_event_binding_spec(text)
                        self.assertEqual('damage.dealt', binding.event)
                        conditions = binding.condition.get('all', [binding.condition])
                        self.assertIn({'field': 'source_attachment_target_ref', 'op': 'eq', 'value': '$context.source'}, conditions)
                        self.assertEqual(bool(combat), any(c.get('field') == 'combat' for c in conditions))
                        self.assertEqual(bool(recipient), any(c.get('field') == 'target_kind' for c in conditions))
                        self.assertEqual(recipient == ' to an opponent', any(c.get('field') == 'target' for c in conditions))
                        ir = compile_oracle_card(replace(query_record(text), type_line='Artifact'), capability_registry=registry)
                        self.assertEqual('exact', ir.status, ir.material_residuals)
                        self.assertEqual(['draw', 'lose_life'], [effect['op'] for effect in ir.faces[0].nodes[0].effects])
                        self.assertIn('attachment.reference.current_or_lki', ir.faces[0].nodes[0].capability_dependencies)

    def test_attached_damage_unknown_headers_bodies_and_ambiguous_players_remain_residual(self):
        registry = load_default_capability_registry()
        for text in ('Whenever enchanted creature deals combat damage to two players, draw a card.',
                     'Whenever equipped creature is dealt combat damage to a player, draw a card.',
                     'Whenever enchanted creatures deal damage, draw a card.',
                     "Whenever enchanted creature deals damage to a player, that creature's controller draws a card.",
                     'Whenever enchanted creature deals damage, draw a card. Rewrite history.'):
            self.assertNotEqual('exact', compile_oracle_card(replace(query_record(text), type_line='Artifact'), capability_registry=registry).status, text)

    def test_attached_damage_owner_withdrawal_blocks_support_and_preserves_unrelated_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        for owner in ('attachment.reference.current_or_lki', 'trigger.event.normalized_damage'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == owner).update(status='blocked', blockers=['attached damage authority withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            text = 'Whenever enchanted creature deals combat damage to a player, draw a card.'
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(replace(query_record('Draw a card.'), type_line='Sorcery'), capability_registry=registry).status)

    def test_omitted_attachment_source_qualifier_mutant_is_killed(self):
        import quorune.compiler.fixed_public_event_trigger_bindings as owner
        original = owner._attachment_attack_or_damage_spec
        text = 'Whenever enchanted creature deals damage, draw a card.'
        def witness():
            binding = owner.fixed_public_event_binding_spec(text)
            self.assertIn({'field': 'source_attachment_target_ref', 'op': 'eq', 'value': '$context.source'}, binding.condition.get('all', [binding.condition]))
        witness()
        def omitted(material):
            binding = original(material)
            if binding is not None and binding.event == 'damage.dealt':
                return replace(binding, condition={'all': [condition for condition in binding.condition.get('all', [binding.condition]) if condition.get('field') != 'source_attachment_target_ref']})
            return binding
        with patch.object(owner, '_attachment_attack_or_damage_spec', side_effect=omitted), self.assertRaises(AssertionError):
            witness()
