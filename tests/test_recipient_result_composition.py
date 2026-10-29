"""Source/recipient binding survives typed result and optional composition."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.event_counter_reference import event_counter_recipient_context, resolve_event_counter_recipient
from quorune.compiler.scalar_effect_amounts import scalar_amount_shape_context
from quorune.scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC
from test_qualified_zone_event_queries import query_record

def record(text, kind='Enchantment — Aura'):
    return replace(query_record(text), type_line=kind)

class RecipientResultCompilerTests(unittest.TestCase):

    def test_receiver_destroy_heads_keep_no_targets_and_per_child_subscriptions(self):
        registry = load_default_capability_registry()
        for head, kind in (('When enchanted creature is dealt damage', 'Enchantment — Aura'), ('Whenever equipped creature is dealt damage', 'Artifact — Equipment'), ('Whenever a creature is dealt damage', 'Enchantment'), ('When enchanted creature becomes tapped or is dealt damage', 'Enchantment — Aura')):
            text = head + ", destroy it. It can't be regenerated."
            ir = compile_oracle_card(record(text, kind), capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node = ir.faces[0].nodes[0]
            self.assertIsNone(node.target_schema)
            self.assertEqual(({'op': 'destroy', 'card': '$event.permanent_object', 'regeneration_prohibited': True},), node.effects)
            self.assertIn('permanent.destroy.regeneration_prohibition', node.capability_dependencies)
        for text in ('When enchanted noncreature is dealt damage, destroy it.', 'Whenever a creature with an unrepresented badge is dealt damage, destroy it.', 'When enchanted creature is dealt damage, destroy it unless its controller pays {1}.'):
            self.assertNotEqual('exact', compile_oracle_card(record(text), capability_registry=registry).status)

    def test_bound_optional_event_quantity_and_source_copy_keep_owned_children(self):
        registry = load_default_capability_registry()
        for text in ('Whenever this creature is dealt damage, you may create that many 1/1 green Insect creature tokens.', "Whenever this creature is dealt damage, you may create a token that's a copy of this creature.", 'Whenever this creature is dealt damage, untap it.'):
            ir = compile_oracle_card(record(text, 'Creature — Insect'), capability_registry=registry)
            self.assertEqual('exact', ir.status, ir.material_residuals)
        for text in ("Whenever a creature is dealt damage, you may create a token that's a copy of it.", 'Whenever a creature is dealt damage, untap it.', 'Whenever this creature is dealt damage, you may create that many 1/1 green Insect creature tokens unless a player pays {1}.'):
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature — Insect'), capability_registry=registry).status, text)

    def test_optional_scalar_projection_only_enters_one_closed_child_result(self):
        text = 'Whenever this creature is dealt damage, you may create that many 1/1 green Insect creature tokens.'
        ir = compile_oracle_card(record(text, 'Creature — Insect'), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertIsNotNone(scalar_amount_shape_context(node.effects, set(node.mechanics)))
        effect = deepcopy(node.effects[0])
        spec = effect['effects'][0]['quantity']
        for changed in ({**effect, 'cost': spec}, {**effect, 'player': spec}, {**effect, 'effects': [{'op': 'create_token', 'selector': spec}]}, {**effect, 'effects': [*effect['effects'], *effect['effects']]}):
            self.assertIsNone(scalar_amount_shape_context((changed,), set(node.mechanics) | {SCALAR_AMOUNT_MECHANIC}))

    def test_sealed_receiving_object_never_uses_giver_or_returned_incarnation(self):
        program = SimpleNamespace(effects=({'op': 'destroy', 'card': '$event.permanent_object'},))
        context = {'card': 'victim', 'target_kind': 'permanent', 'target_logical_object_id': 'victim@1', 'source_logical_object_id': 'giver@1'}
        sealed = event_counter_recipient_context(program, 'damage.received', context)
        victim = SimpleNamespace(ref='victim', zone='battlefield', phased_out=False, logical_object_id='victim@1')
        host = SimpleNamespace(state=SimpleNamespace(cards={'victim': victim}))
        item = SimpleNamespace(context=sealed)
        self.assertEqual('victim', resolve_event_counter_recipient(host, item))
        victim.logical_object_id = 'victim@2'
        self.assertIsNone(resolve_event_counter_recipient(host, item))
        with self.assertRaises(ValueError):
            event_counter_recipient_context(program, 'damage.received', {**context, 'target_kind': 'player'})

    def test_receiver_result_minima_and_scope_survive_removed_coverage(self):
        from quorune.card_programs import compile_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        registry = load_default_capability_registry()
        db = type('NoRulings', (), {'rulings': lambda self, record: ()})()
        p = compile_card_program(db, record("Whenever a creature is dealt damage, destroy it. It can't be regenerated.", 'Enchantment'), trust_level='trusted', capability_registry=registry, capability_profile='commander_review').abilities[0]
        for removed in ('resolution.event_object.fixed_result', 'permanent.destroy.regeneration_prohibition', 'trigger.event.normalized_received_damage'):
            deps = [c for c in p.capability_dependencies if c != removed]
            changed = replace(p, coverage=[], capability_dependencies=deps, capability_closure=registry.closure(deps, profile='commander_review').to_dict())
            binding = bind_semantic_program_runtime(changed, capability_registry=registry, profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:' + removed, binding['blockers'])
        changed = replace(p, event_condition=None)
        self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=registry, profile='commander_review')['strict'])

    def test_recipient_or_binding_optional_producer_omission_mutants_are_killed(self):

        def receiver():
            self.assertEqual('exact', compile_oracle_card(record('When enchanted creature is dealt damage, destroy it.'), capability_registry=load_default_capability_registry()).status)
        receiver()
        with patch('quorune.compiler.received_damage_bindings.received_permanent_binding_spec', return_value=None), self.assertRaises(AssertionError):
            receiver()

        def optional():
            self.assertEqual('exact', compile_oracle_card(record('Whenever this creature is dealt damage, you may create that many 1/1 green Insect creature tokens.', 'Creature — Insect'), capability_registry=load_default_capability_registry()).status)
        optional()
        with patch('quorune.compiler.binding_optional_results.binding_aware_optional_result', return_value=None), self.assertRaises(AssertionError):
            optional()

    def test_verified_result_owner_withdrawal_keeps_ordinary_draw_and_optional_counter_boundary(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        original = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text())
        decl = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        for owner, text in (('resolution.event_object.fixed_result', 'When enchanted creature is dealt damage, destroy it.'), ('permanent.destroy.regeneration_prohibition', "Whenever a creature is dealt damage, destroy it. It can't be regenerated."), ('counter.producer.optional_fixed_event_trigger', 'Whenever this creature is dealt damage, you may put a +1/+1 counter on it.')):
            raw = deepcopy(original)
            next((r for r in raw['capabilities'] if r['id'] == owner)).update(status='blocked', blockers=['result owner withdrawn'])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw, declaration_source=decl, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature — Insect' if 'counter' in text else 'Enchantment — Aura'), capability_registry=registry).status)
            self.assertEqual('exact', compile_oracle_card(record('Draw a card.', 'Sorcery'), capability_registry=registry).status)
