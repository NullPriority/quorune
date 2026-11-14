"""Resolved attack-if-able requirements apply to each combat of this turn."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from inherited_composition_fixture import InheritedNativeFixture
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from unittest.mock import patch
import test_recipient_control as seat_actions

ORIGINALS = ('Into the Fray', 'Basandra, Battle Seraph', 'Heckling Fiends',
    "Imps' Taunt", 'Rage Nimbus', 'Boiling Blood')


class ResolvedAttackRequirementCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'resolved-attacks.sqlite3'
        build_fixture_database(ROOT / 'tests/fixtures/resolved-attack-requirement-cards.json', path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.registry,
            capability_profile='commander_review')

    def test_six_originals_compile_the_complete_this_turn_requirement(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                ir = self.compile(self.db.lookup(name))
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertTrue(any('Target creature attacks this turn if able.' in node.text
                    and (node.effects or node.handlers) for face in ir.faces for node in face.nodes))

    def test_unrepresented_duration_recipient_and_extra_demand_remain_material(self):
        record = self.db.lookup('Boiling Blood')
        for text in ('Target creature attacks next turn if able.',
                'Target creature attacks this turn if able and attacks an opponent other than you if able.',
                'All creatures attack this turn if able.',
                'Target creature attacks this turn if able unless its controller pays {1}.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(replace(record, oracle_text=text)).status)


class ResolvedAttackRequirementNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name) / 'attack-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/resolved-attack-requirement-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Resolved attack requirements',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def scene(self, name='Boiling Blood', *, tax=False, pacifism=False, haste=False, splice=False):
        s = self.session(508611 + len(name) + tax + pacifism)
        activated = name in ('Basandra, Battle Seraph', 'Heckling Fiends', 'Rage Nimbus')
        source = self.add(s.engine, name, seat='B', zone='battlefield' if activated else 'hand')
        target = self.add(s.engine, 'Grizzly Bears', ref='required-attacker')
        target.acquired_control_turn_count = -1
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        blink = self.add(s.engine, 'Cloudshift', zone='hand')
        assault = self.add(s.engine, 'Relentless Assault', zone='hand')
        pacify = self.add(s.engine, 'Pacifism', zone='hand')
        if splice: self.add(s.engine, 'Reach Through Mists', seat='B', zone='hand', ref='arcane-host')
        for seat in 'AB':
            for index, land in enumerate(('Mountain',)*6 + ('Island', 'Plains', 'Swamp', 'Forest', 'Forest')):
                self.add(s.engine, land, seat=seat, ref=seat+'-requirement-mana-'+str(index))
        if tax:
            for seat in 'BCD': self.add(s.engine, 'Ghostly Prison', seat=seat, ref='prison-'+seat)
        if haste: self.add(s.engine, 'Fervor')
        self.assert_closed(s, source); s.state.config.semantic_policy = 'trusted_only'
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        s.state.active_player = 'A'; s.state.started = True
        s.state.phase = 'precombat_main'; s.state.step = 'main'; s.state.phase_index = 3
        s.engine._grant_priority('A'); s.engine.pump(); self.checkpoint(s)
        return s, source, target, private, bounce, blink, assault, pacify

    def impose(self, s, source, target):
        if source.zone == 'hand': self.cast(s, 'B', source, [target.ref])
        else:
            self.priority(s, 'B')
            action = next(a for a in s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                if a['id'].startswith('activate:'+source.ref+':'))
            command = {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}
            self.reject(s, 'D', command)
            result = s.act('pilot:B', command); self.assertTrue(result.ok, result.summary)

    def declaration(self, s):
        self.until(s, lambda current: current.state.pending_decision is not None
            and (current.state.pending_decision.kind == 'combat.attackers'
                or current.state.phase == 'combat' and current.state.step == 'declare_attackers'
                and current.state.combat.attackers_declared))

    def omit(self, s, *, allowed):
        self.declaration(s); command = {'action_id': 'attack', 'attackers': {}}
        if s.state.combat.attackers_declared:
            self.assertTrue(allowed, 'A required legal attacker must not be automatically omitted')
            self.assertFalse(s.state.combat.attackers)
            return
        before = authoritative_state_hash(s.state)
        self.reject(s, 'D', command)
        result = s.act('pilot:A', command); self.assertEqual(allowed, result.ok, result.summary)
        if not allowed: self.assertEqual(before, authoritative_state_hash(s.state))

    def original(self, name):
        s, source, target, private, *_ = self.scene(name)
        hand = len(s.state.players['B'].zones['hand']); self.impose(s, source, target)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        if name == 'Boiling Blood': self.assertEqual(hand, len(s.state.players['B'].zones['hand']))
        self.omit(s, allowed=False)
        # The effect's controller is a legal defender: this instruction is not goad.
        self.attack(s, [(target, 'B')])
        self.assertEqual('B', s.state.combat.attackers[target.object_id]); self.finish(s, private)

    def test_all_six_original_spells_and_activations_require_an_attack_without_forcing_a_defender(self):
        for name in ORIGINALS:
            with self.subTest(name=name): self.original(name)

    def test_fresh_original_witness_requires_the_runtime_requirement_owner(self):
        self.original('Boiling Blood')

    def test_actual_arcane_splice_preserves_the_requirement_and_selected_source_in_hand(self):
        s, source, target, private, *_ = self.scene('Into the Fray', splice=True)
        host = next(c for c in s.state.cards.values() if c.ref == 'arcane-host')
        self.assert_closed(s, host); self.priority(s, 'B')
        offer = next(a for a in s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
            if a['id'] == 'cast:'+host.ref)
        self.assertIn('prepare_splice', offer['choice_schema'])
        result = s.act('pilot:B', {'action_id': offer['id'], 'prepare_splice': True})
        self.assertTrue(result.ok, result.summary); self.assertEqual('selection.splice_cast', s.state.pending_decision.kind)
        self.reject(s, 'D', {'action_id': 'add:'+source.ref})
        result = s.act('pilot:B', {'action_id': 'add:'+source.ref}); self.assertTrue(result.ok, result.summary)
        result = s.act('pilot:B', {'action_id': 'cast', 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertEqual(['draw', 'grant_declaration_restriction_until_end_of_turn'],
            [effect['op'] for effect in s.state.stack[-1].context['cast_option_effects']])
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.omit(s, allowed=False); self.attack(s, [(target, 'B')]); self.finish(s, private)

    def test_resolution_owner_omission_mutant_is_killed(self):
        method = 'test_fresh_original_witness_requires_the_runtime_requirement_owner'
        with patch('quorune.continuous_effect_state.active_resolution_declaration_rule_effects', return_value=()):
            with self.assertRaises(AssertionError): getattr(type(self)(method), method)()

    def test_runtime_minimum_owners_and_compiler_producer_cannot_be_withheld(self):
        from quorune.temporary_attack_requirement_model import TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s = self.session(508614); source = self.add(s.engine, 'Boiling Blood', seat='B', zone='hand')
        self.assert_closed(s, source)
        program, = [p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'resolve']
        broadened = replace(program, target_schema={**program.target_schema, 'types_any': ['creature', 'artifact']})
        self.assertFalse(bind_semantic_program_runtime(broadened, capability_registry=self.registry,
            profile='commander_review')['strict'])
        for cap in (TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY, 'combat.declaration.typed_components',
                'continuous.resolution.declaration_rules_until_end_of_turn', 'target.revalidate_resolution'):
            with self.subTest(cap=cap):
                deps = tuple(c for c in program.capability_dependencies if c != cap)
                changed = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                    profile='commander_review')
                self.assertFalse(bound['strict']); self.assertIn('capability:undeclared_runtime_dependency:'+cap, bound['blockers'])
                raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(row for row in raw['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['withdrawn attack requirement owner'])
                self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Boiling Blood'),
                    capability_registry=CapabilityRegistry(raw), capability_profile='commander_review').status)
        with patch('quorune.compiler.temporary_declaration_templates.temporary_attack_requirement_template', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Boiling Blood'),
                capability_registry=self.registry, capability_profile='commander_review').status)

    def test_optional_attack_costs_are_not_forced_and_attack_prohibitions_win(self):
        for tax in (True, False):
            with self.subTest(tax=tax):
                s, source, target, private, bounce, blink, assault, pacify = self.scene(tax=tax)
                self.impose(s, source, target); self.assertIsNone(self.resolve(s))
                if not tax: self.cast(s, 'A', pacify, [target.ref]); self.assertIsNone(self.resolve(s))
                self.omit(s, allowed=True); self.finish(s, private)

    def test_a_blinked_creature_is_a_new_incarnation_even_when_it_can_attack(self):
        s, source, target, private, bounce, blink, assault, pacify = self.scene(haste=True)
        self.impose(s, source, target); self.assertIsNone(self.resolve(s)); old = target.logical_object_id
        self.cast(s, 'A', blink, [target.ref]); self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old, s.state.cards[target.object_id].logical_object_id)
        self.assertIn('haste', s.engine._combat_keywords(s.state.cards[target.object_id]))
        self.omit(s, allowed=True); self.finish(s, private)

    def test_illegal_sole_target_creates_no_requirement_on_a_recast_creature(self):
        s, source, target, private, bounce, blink, assault, pacify = self.scene(haste=True)
        self.impose(s, source, target); self.cast(s, 'A', bounce, [target.ref]); self.resolve_top(s)
        self.assertIsNone(self.resolve(s)); self.assertEqual('hand', s.state.cards[target.object_id].zone)
        self.cast(s, 'A', s.state.cards[target.object_id]); self.assertIsNone(self.resolve(s))
        self.omit(s, allowed=True); self.finish(s, private)

    def test_same_turn_additional_combat_requires_another_attack_then_requirement_expires(self):
        s, source, target, private, bounce, blink, assault, pacify = self.scene()
        self.impose(s, source, target); self.assertIsNone(self.resolve(s)); self.attack(s, [(target, 'B')])
        self.until(s, lambda current: current.state.phase == 'postcombat_main'
            and current.state.pending_decision is not None and current.state.pending_decision.kind == 'priority')
        self.cast(s, 'A', assault); s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[target.object_id].tapped); self.omit(s, allowed=False)
        self.attack(s, [(s.state.cards[target.object_id], 'C')])
        self.assertEqual(('C',), s.state.combat.declared_player_attacks.players)
        self.until(s, lambda current: current.state.active_player == 'B'
            and current.state.step == 'upkeep')
        from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
        self.assertFalse(any(isinstance(effect, ResolutionDeclarationRuleEffect)
            and getattr(effect.restriction, 'kind', None) == 'attack_each_combat'
            for effect in s.state.continuous_effects or ()))
        self.finish(s, private)


class ResolvedAttackRequirementModelTests(unittest.TestCase):
    def test_closed_versioned_spec_and_existing_journal_round_trip(self):
        from quorune.temporary_attack_requirement_model import TemporaryAttackRequirementSpec
        from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        spec = TemporaryAttackRequirementSpec(); self.assertEqual(spec, type(spec).from_dict(spec.to_dict()))
        effect = ResolutionDeclarationRuleEffect('E1', 'S1', 1, spec.to_requirement(), (ContinuousObjectIdentity('C1', 'C1@1'),))
        self.assertEqual(effect.to_dict(), type(effect).from_dict(effect.to_dict()).to_dict())
        self.assertEqual('attack_each_combat', effect.restriction.kind)
        for changed in ({**spec.to_dict(), 'schema_version': True}, {**spec.to_dict(), 'kind': 'goad'},
                {**spec.to_dict(), 'extra': 1}):
            with self.assertRaises(ValueError): type(spec).from_dict(changed)


if __name__ == '__main__': unittest.main()
