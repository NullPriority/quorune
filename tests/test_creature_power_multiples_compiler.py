"""Closed version-two doubling preserves the legacy power-damage descriptor."""
from copy import deepcopy
import json
import unittest
from common import ROOT
import test_fixed_creature_power_damage as controls
from quorune.oracle_ir import compile_oracle_card
from quorune.creature_power_damage_model import creature_power_damage_multiplier, CREATURE_POWER_DAMAGE_MULTIPLE_CAPABILITY as CAPABILITY
from quorune.rules.creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from quorune.rules.capabilities import load_default_capability_registry, CapabilityRegistry

TEXT = "Target creature you control deals damage equal to twice its power to target creature you don't control."


class CreaturePowerMultipleCompilerTests(unittest.TestCase):
    def compile(self, text, registry=None):
        return compile_oracle_card(controls.card_record(text), capability_registry=registry or load_default_capability_registry())

    def test_power_multiple_codec_preserves_legacy_and_rejects_malformed_extensions(self):
        legacy = {'op': 'creature_power_damage', 'kind': 'bite', 'source': '$target.0', 'target': '$target.1', 'target_must_be_creature': True, 'source_lki': None}
        self.assertEqual(1, creature_power_damage_multiplier(legacy))
        doubled = {**legacy, 'schema_version': 2, 'power_multiplier': 2}
        self.assertEqual(2, creature_power_damage_multiplier(doubled))
        for change in ({'schema_version': True}, {'schema_version': 1}, {'schema_version': 3}, {'power_multiplier': True}, {'power_multiplier': 1}, {'power_multiplier': 3}, {'power_multiplier': 2.0}, {'kind': 'fight'}):
            with self.subTest(change=change), self.assertRaises(ValueError):creature_power_damage_multiplier({**doubled, **change})
        for omitted in ('schema_version', 'power_multiplier'):
            malformed = dict(doubled);malformed.pop(omitted)
            with self.assertRaises(ValueError):creature_power_damage_multiplier(malformed)

    def test_power_multiple_compiler_emits_explicit_scale_and_keeps_ordinary_and_fight_bytes(self):
        ir = self.compile(TEXT);self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0];effect, = node.effects
        self.assertEqual((0, len(TEXT)), (node.span.start, node.span.end))
        self.assertEqual(2, effect['schema_version']);self.assertEqual(2, effect['power_multiplier'])
        self.assertEqual(('$target.0', '$target.1', 'bite'), tuple(effect[k] for k in ('source', 'target', 'kind')))
        self.assertIn(CAPABILITY, node.capability_dependencies)
        plain = self.compile(TEXT.replace('twice ', ''));ordinary, = plain.faces[0].nodes[0].effects
        self.assertEqual({k:v for k,v in effect.items() if k not in {'schema_version','power_multiplier'}}, ordinary)
        fight = self.compile('Target creature you control fights target creature an opponent controls.')
        self.assertEqual('exact', fight.status);self.assertNotIn('schema_version', fight.faces[0].nodes[0].effects[0])
        for text in (TEXT.replace('twice', 'three times'), TEXT.replace('its power', 'its toughness'), TEXT.replace('Target creature you control', 'It')):
            self.assertNotEqual('exact', self.compile(text).status, text)

    def test_power_multiple_payload_requires_its_capability_without_annotations(self):
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.semantics import SemanticProgram
        node = self.compile(TEXT).faces[0].nodes[0]
        self.assertIn(CAPABILITY, typed_result_amount_runtime_dependencies(node.effects))
        effects = json.loads(json.dumps(node.effects));effects[0].pop('schema_version');effects[0].pop('power_multiplier')
        self.assertNotIn(CAPABILITY, typed_result_amount_runtime_dependencies(effects))
        registry = load_default_capability_registry();dependencies = tuple(c for c in node.capability_dependencies if c != CAPABILITY)
        program = SemanticProgram(key='fixture:power-multiple', label='Fixed doubled damage', oracle_id='fixture:power', ability_id='spell:front', active_zone='stack', event='spell.cast', effects=list(node.effects), target_schema=node.target_schema,
            coverage=list(node.mechanics), capability_dependencies=dependencies, capability_closure=registry.closure(dependencies, profile='commander_review').to_dict())
        binding = bind_semantic_program_runtime(program, capability_registry=registry, profile='commander_review')
        self.assertIn(CAPABILITY, binding['required_registered_capabilities']);self.assertFalse(binding['strict'])
        for change in ({'power_multiplier': 3}, {'kind': 'fight'}, {'source': '$source'}):
            changed = json.loads(json.dumps(node.effects));changed[0].update(change)
            self.assertEqual((), fixed_creature_power_damage_node_capabilities(effects=changed, target_schema=node.target_schema, mechanic_ids=node.mechanics))

    def test_power_multiple_verified_withdrawal_rejects_new_form_and_retains_plain_damage(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text());next(r for r in raw['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['Fixed multiplier withdrawn'])
        registry = CapabilityRegistry(raw);index = build_index(registry_value=raw, declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', self.compile(TEXT, registry).status)
        self.assertEqual('exact', self.compile(TEXT.replace('twice ', ''), registry).status)
