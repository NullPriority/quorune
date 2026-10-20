from __future__ import annotations

"""CR702.16/400.7/602.2b: fixed protection on the actual source incarnation."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import ability_fragment_from_dict, protection_specs
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.compiler.source_fixed_protection import CAPABILITY, source_fixed_protection_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.source_fixed_protection_shapes import source_fixed_protection_capabilities
from scripts.build_test_database import build_fixture_database
import test_source_protection_choices as controls


NAMES = ('Keeper of Kookus', 'Hell-Bent Raider')


class SourceFixedProtectionTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.SourceProtectionChoiceTests.session
    add = controls.SourceProtectionChoiceTests.add
    prepare = controls.SourceProtectionChoiceTests.prepare
    checkpoint = controls.SourceProtectionChoiceTests.checkpoint
    cast = controls.SourceProtectionChoiceTests.cast
    resolve = controls.SourceProtectionChoiceTests.resolve
    replay = controls.SourceProtectionChoiceTests.replay
    binding = controls.SourceProtectionChoiceTests.binding
    actions = controls.SourceProtectionChoiceTests.actions
    private_replay = controls.SourceProtectionChoiceTests.private_replay
    priority = controls.SourceProtectionChoiceTests.priority
    advance = controls.SourceProtectionChoiceTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'source-fixed-protection.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/source-fixed-protection-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed self protection',
            [DeckEntry('Generic Bound Commander', 1, 'commander'),
             DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def protection(self, session, source):
        card = session.state.cards[source.object_id]
        return {value.quality for value in protection_specs(session.engine._effective_ability_fragments(card))}

    def activation(self, session, source):
        return next(row for row in self.actions(session) if row['id'].startswith('activate:' + source.ref + ':'))

    def test_actual_keeper_pays_red_then_counters_a_real_bolt_without_a_color_choice(self):
        session = self.session(741100)
        source = self.add(session.engine, 'Keeper of Kookus')
        bolt = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'R': 1})
        session.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(session)
        self.priority(session, 'B')
        self.cast(session, 'B', bolt, [source.ref])
        self.priority(session, 'A')
        action = self.activation(session, source)
        command = {'action_id': action['id'], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, session.state.players['A'].mana_pool['R'])
        self.assertEqual(set(), self.protection(session, source))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'R'}, self.protection(session, source))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.cards[source.object_id].marked_damage)
        self.private_replay(session, private)

    def test_two_originals_have_complete_nonempty_fixed_source_programs(self):
        for index, name in enumerate(NAMES):
            with self.subTest(name=name):
                session = self.session(741110 + index)
                source = self.add(session.engine, name)
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertTrue(self.binding(session, source)['strict_capability_ready'])
                self.assertTrue(any(program.effects for program in
                    session.engine.semantics.programs_for_oracle(source.oracle_id)))

    def test_actual_raider_random_discard_removes_white_aura_and_preserves_printed_keywords(self):
        session = self.session(741120)
        source = self.add(session.engine, 'Hell-Bent Raider')
        aura = self.add(session.engine, 'Pacifism', zone='hand')
        suns = [self.add(session.engine, 'Sunlance', zone='hand', ref='white-target-control-' + str(i)) for i in range(2)]
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'W': 3, 'C': 1})
        self.checkpoint(session)
        self.cast(session, 'A', aura, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(source.object_id, session.state.cards[aura.object_id].attached_to)
        hand = set(session.state.players['A'].zones['hand'])
        command = {'action_id': self.activation(session, source)['id'], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        discarded = hand - set(session.state.players['A'].zones['hand'])
        self.assertEqual(1, len(discarded))
        self.assertEqual('graveyard', session.state.cards[next(iter(discarded))].zone)
        self.assertEqual(set(), self.protection(session, source))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'W'}, self.protection(session, source))
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        words = session.engine._combat_keywords(session.state.cards[source.object_id])
        self.assertTrue({'first strike', 'haste'} <= words)
        remaining = next(card for card in suns if session.state.cards[card.object_id].zone == 'hand')
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:A', {'action_id': 'cast:' + remaining.ref,
            'targets': [source.ref], 'pay': 'auto'})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        for object_id in session.state.players['A'].zones['hand']:
            for viewer in 'BCD':
                self.assertNotIn(session.state.cards[object_id].ref,
                    json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.private_replay(session, private)

    def test_fixed_white_protection_does_not_prevent_a_real_red_bolt(self):
        session = self.session(741121)
        source = self.add(session.engine, 'Hell-Bent Raider')
        bolt = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        self.prepare(session, mana={})
        session.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': self.activation(session, source)['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'W'}, self.protection(session, source))
        self.priority(session, 'B')
        self.cast(session, 'B', bolt, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_fixed_source_reference_does_not_follow_a_blinked_incarnation(self):
        session = self.session(741122)
        source = self.add(session.engine, 'Keeper of Kookus')
        blink = self.add(session.engine, 'Ephemerate', zone='hand')
        self.prepare(session, mana={'R': 1, 'W': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': self.activation(session, source)['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        old = source.logical_object_id
        self.cast(session, 'A', blink, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(old, session.state.cards[source.object_id].logical_object_id)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(set(), self.protection(session, source))
        self.replay(session, load=True)

    def test_fixed_source_grant_tracks_control_change_and_expires_at_cleanup(self):
        session = self.session(741123)
        source = self.add(session.engine, 'Keeper of Kookus')
        steal = self.add(session.engine, 'Word of Seizing', seat='B', zone='hand')
        self.prepare(session, mana={'R': 1})
        session.state.players['B'].mana_pool.update({'R': 2, 'C': 3})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': self.activation(session, source)['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.priority(session, 'B')
        self.cast(session, 'B', steal, [source.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        self.assertEqual({'R'}, self.protection(session, source))
        turn = session.state.turn_sequence
        self.advance(session, lambda s: s.state.turn_sequence > turn)
        self.assertEqual('A', session.state.cards[source.object_id].controller)
        self.assertEqual(set(), self.protection(session, source))
        self.replay(session, load=True)

    def test_fresh_keeper_witness_kills_omitted_compiler_result(self):
        method = 'test_actual_keeper_pays_red_then_counters_a_real_bolt_without_a_color_choice'
        with patch('quorune.compiler.resolution_effect_templates.source_fixed_protection_template', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_keeper_witness_kills_omitted_protection_grant(self):
        method = 'test_actual_keeper_pays_red_then_counters_a_real_bolt_without_a_color_choice'
        with patch('quorune.effect_runtime.temporary_ability_bundle.create_resolution_continuous_effect', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_direct_bundle_shape_is_closed_for_every_fixed_color(self):
        for word, symbol in (('white', 'W'), ('blue', 'U'), ('black', 'B'), ('red', 'R'), ('green', 'G')):
            with self.subTest(word=word):
                template, effects, targets, mechanics = source_fixed_protection_template(
                    'This creature gains protection from ' + word + ' until end of turn.',
                    source_is_permanent=True, source_card_types=('creature',), source_name='Generic Source')
                self.assertEqual('temporary-source-fixed-protection-v1', template)
                self.assertIsNone(targets)
                self.assertIn(CAPABILITY, source_fixed_protection_capabilities(
                    effects=effects, target_schema=targets, mechanic_ids=mechanics))
                self.assertEqual(symbol, ability_fragment_from_dict(effects[0]['ability_fragments'][0]).quality)
                for field, value in (('schema_version', True), ('card', '$target.0'),
                        ('keywords', ['Flying']), ('exclude_source', True), ('extra', 1)):
                    altered = deepcopy(effects)
                    altered[0][field] = value
                    self.assertEqual((), source_fixed_protection_capabilities(
                        effects=altered, target_schema=None, mechanic_ids=mechanics))
                self.assertEqual((), source_fixed_protection_capabilities(
                    effects=effects, target_schema={'groups': []}, mechanic_ids=mechanics))

    def test_minimum_source_protection_and_duration_authority_cannot_be_omitted(self):
        session = self.session(741130)
        source = self.add(session.engine, 'Keeper of Kookus')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(e.get('op') == 'apply_source_characteristics_until_end_of_turn' for e in p.effects)]
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict'])
        for cap in (CAPABILITY, 'protection.typed.debt', 'continuous.resolution.fixed_characteristics_until_end_of_turn'):
            with self.subTest(cap=cap):
                deps = tuple(x for x in program.capability_dependencies if x != cap)
                missing = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])
                raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(x for x in raw['capabilities'] if x['id'] == cap).update(status='blocked', blockers=['withdrawn fixed source protection'])
                self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Keeper of Kookus'),
                    capability_registry=CapabilityRegistry(raw), capability_profile='commander_review').status)

    def test_unknown_qualities_and_independent_echo_permission_remain_residual(self):
        for text in ('This creature gains protection from the color of its controller choice until end of turn.',
                'This creature gains protection from artifacts until end of turn.',
                'This creature gains protection from red and blue until end of turn.',
                'Target creature gains protection from red until end of turn.',
                'This creature gains protection from red until your next turn.'):
            self.assertIsNone(source_fixed_protection_template(text, source_is_permanent=True,
                source_card_types=('creature',), source_name='Generic Source'))
        ir = compile_oracle_card(self.db.lookup('Thick-Skinned Goblin'), capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(any('echo cost' in node.text for face in ir.faces for node in face.nodes if not node.exact))
