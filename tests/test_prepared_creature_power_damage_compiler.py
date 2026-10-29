"""Independent binding and closure boundaries for prepared power damage."""
from copy import deepcopy
import json
import unittest
from common import ROOT
import test_fixed_creature_power_damage as controls
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry, CapabilityRegistry
from quorune.rules.prepared_creature_power_damage_shapes import (
    PREPARED_POWER_DAMAGE_CAPABILITY, prepared_creature_power_damage_capabilities,
)


class PreparedPowerDamageCompilerTests(unittest.TestCase):
    def compile(self, text, registry=None):
        return compile_oracle_card(controls.card_record(text), capability_registry=registry or load_default_capability_registry())

    def test_prepared_damage_compiler_preserves_source_and_recipient_roles(self):
        for prefix in ('Target creature you control gets +1/+0 until end of turn.',
                       'Put a +1/+1 counter on target creature you control.',
                       'Target creature you control gains vigilance and trample until end of turn.',
                       'Target creature you control gets +1/+0 and gains vigilance until end of turn.'):
            for recipient in ("target creature you don't control", 'target creature or planeswalker an opponent controls'):
                text = prefix + ' Then that creature deals damage equal to its power to ' + recipient + '.'
                with self.subTest(text=text):
                    ir = self.compile(text)
                    self.assertEqual('exact', ir.status, ir.material_residuals)
                    node = ir.faces[0].nodes[0]
                    self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                    self.assertIn(PREPARED_POWER_DAMAGE_CAPABILITY, node.capability_dependencies)
                    self.assertTrue(all(e['card'] == '$target.0' for e in node.effects[:-1]))
                    self.assertEqual(('$target.0', '$target.1', 'bite'), tuple(node.effects[-1][k] for k in ('source', 'target', 'kind')))
                    self.assertEqual('you', node.target_schema['groups'][0]['controller_relation'])
                    self.assertEqual('opponent', node.target_schema['groups'][1]['controller_relation'])

    def test_prepared_damage_ambiguous_and_unrepresented_variants_remain_residual(self):
        for text in (
            'Target creature you control gets +X/+0 until end of turn. It deals damage equal to its power to target creature.',
            'Target creature gets +1/+0 until end of turn. It deals damage equal to its power to target creature.',
            'Target creature you control gets +1/+0 until end of turn. It deals damage equal to twice its power to target creature.',
            'Target creature you control gets +1/+0 until end of turn. Another creature deals damage equal to its power to target creature.',
            'Put a +1/+1 counter on target creature you control if it is legendary. It deals damage equal to its power to target creature.',
            'Target creature you control gets +1/+0 until end of turn. It deals damage equal to its toughness to target creature.',
        ):
            self.assertNotEqual('exact', self.compile(text).status, text)
        comma = 'Put a +1/+1 counter on target creature you control, then it deals damage equal to its power to target creature an opponent controls.'
        self.assertEqual('exact', self.compile(comma).status)
        for quality in ('green creature',):
            text = f'Target {quality} you control gets +1/+0 until end of turn. It deals damage equal to its power to target creature an opponent controls.'
            self.assertEqual('exact', self.compile(text).status, text)
        subtype = 'Target Beast creature you control gets +1/+0 until end of turn. It deals damage equal to its power to target creature an opponent controls.'
        self.assertNotEqual('exact', self.compile(subtype).status)
        unrelated_counter = 'Put a charge counter on target creature you control. It deals damage equal to its power to target creature an opponent controls.'
        self.assertNotEqual('exact', self.compile(unrelated_counter).status)

    def test_prepared_damage_descriptor_mutations_reject_changed_binding_and_results(self):
        ir = self.compile("Target creature you control gains vigilance and trample until end of turn. It deals damage equal to its power to target creature you don't control.")
        node = ir.faces[0].nodes[0]
        effects = json.loads(json.dumps(node.effects));schema = json.loads(json.dumps(node.target_schema));mechanics = node.mechanics
        def closed(e=effects, s=schema):
            return prepared_creature_power_damage_capabilities(effects=e, target_schema=s, mechanic_ids=mechanics)
        self.assertIn(PREPARED_POWER_DAMAGE_CAPABILITY, closed())
        for index, field, value in ((0, 'card', '$target.1'), (1, 'keyword', 'Unknown'),
                                    (2, 'source', '$source'), (2, 'target', '$target.0'),
                                    (2, 'kind', 'fight'), (2, 'source_lki', {})):
            mutated = deepcopy(effects);mutated[index][field] = value
            self.assertEqual((), closed(mutated), (index, field))
        changed = deepcopy(schema);changed['groups'][0]['count'] = 2
        self.assertEqual((), closed(s=changed))
        changed = deepcopy(schema);changed['groups'][0]['controller_relation'] = 'opponent'
        self.assertEqual((), closed(s=changed))

    def test_prepared_damage_verified_capability_withdrawal_retains_plain_bite(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        next(r for r in raw['capabilities'] if r['id'] == PREPARED_POWER_DAMAGE_CAPABILITY).update(status='blocked', blockers=['Preparation authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        body = "Target creature you control gets +1/+0 until end of turn. It deals damage equal to its power to target creature you don't control."
        self.assertNotEqual('exact', self.compile(body, registry).status)
        self.assertEqual('exact', self.compile("Target creature you control deals damage equal to its power to target creature you don't control.", registry).status)
