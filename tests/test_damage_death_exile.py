"""Damage, locked incarnations, and ordinary graveyard replacement authority."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.damage_death_exile_model import (
    DAMAGE_DEATH_EXILE_CAPABILITY, DAMAGE_DEATH_EXILE_FIELD, DamageDeathExileScope, ResolvedDamageDeathExile,
)
from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules import damage_death_exile as owner
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


class DamageDeathExileTests(unittest.TestCase):
    def test_compiler_scope_is_closed_and_requires_replacement_authority(self):
        registry = load_default_capability_registry()
        body = 'Generic Bound Compiler Fixture deals 2 damage to target creature.'
        spell = body + ' If that creature would die this turn, exile it instead.'
        def compile_source(text, caps=registry):
            return compile_oracle_card(record(text), capability_registry=caps, capability_profile='commander_review')

        ir = compile_source(spell)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(DamageDeathExileScope.TARGET.value, node.effects[0][DAMAGE_DEATH_EXILE_FIELD])
        self.assertIn(DAMAGE_DEATH_EXILE_CAPABILITY, node.capability_dependencies)
        for text in (spell.replace('target creature', 'any target'), spell.replace('this turn', 'next turn'),
                     spell.replace('exile it instead', 'return it to hand instead')):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_source(text).status)
        payload = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        next(row for row in payload['capabilities'] if row['id'] == DAMAGE_DEATH_EXILE_CAPABILITY).update(
            status='blocked', blockers=['withdrawn replacement authority'])
        withdrawn = CapabilityRegistry(payload)
        withdrawn.mark_evidence_verified(registry.evidence_fingerprint)
        self.assertNotEqual('exact', compile_source(spell, withdrawn).status)

    def test_committed_recipient_selection_and_journal_codec_reject_mutants(self):
        original = SimpleNamespace(kind='permanent', types=('creature',), object_id='first', logical_object_id='first@1')
        redirected = SimpleNamespace(was_dealt=True, target_kind='permanent', target_types=('creature',),
                                     target_object_id='second', target_logical_object_id='second@2')
        first = ContinuousObjectIdentity('first', 'first@1')
        second = ContinuousObjectIdentity('second', 'second@2')

        def witness(scope, expected):
            state = SimpleNamespace(continuous_effects=[])
            host = SimpleNamespace(state=state, _next_ref=lambda prefix: prefix+'1', _next_zone_timestamp=lambda: 9)
            owner.commit_damage_death_exile(host, scope=scope, source_ref='spell',
                                           events=(redirected,), original_target=original)
            rule, = state.continuous_effects
            self.assertEqual((expected,), rule.locked_objects)
            self.assertEqual(rule, continuous_journal_effect_from_dict(rule.to_dict()))
            return rule

        witness(DamageDeathExileScope.TARGET, first)
        rule = witness(DamageDeathExileScope.DEALT_CREATURES, second)
        with patch.object(owner, 'damage_death_exile_subjects', return_value=(first,)):
            with self.assertRaises(AssertionError):
                witness(DamageDeathExileScope.DEALT_CREATURES, second)
        with self.assertRaises(ValueError):
            continuous_journal_effect_from_dict({**rule.to_dict(), 'timestamp': True})
        redirected.was_dealt = False
        self.assertEqual((), owner.damage_death_exile_subjects(DamageDeathExileScope.DEALT_CREATURES, (redirected,), original))


class DamageDeathExileNativeTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'damage-death-exile.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/damage-death-exile-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Damage death exile', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 25),
        ], ['Generic Bound Commander'])

    def prevent(self, session, seat, salve, creature):
        self.priority(session, seat)
        action = self.action(session, seat, salve)
        result = session.act('pilot:'+seat, {'action_id': action['id'], 'modes': ['mode_2'],
                                            'targets': [creature.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)

    def test_original_target_and_dealt_scopes_differ_after_prevention_and_expire(self):
        cases = (('Magma Spray', 'same', 'exile'), ('Pillar of Flame', 'same', 'graveyard'),
                 ('Magma Spray', 'blink', 'graveyard'), ('Magma Spray', 'cleanup', 'graveyard'))
        for index, (name, transition, destination) in enumerate(cases):
            with self.subTest(name=name, transition=transition):
                session = self.session(705_614_601 + index)
                damage = self.add(session.engine, name, zone='hand')
                kill = self.add(session.engine, 'Doom Blade', zone='hand')
                creature = self.add(session.engine, 'Grizzly Bears', seat='B')
                salve = self.add(session.engine, 'Healing Salve', seat='B', zone='hand')
                self.add(session.engine, 'Plains', seat='B', ref='salve-mana')
                self.add(session.engine, 'Swamp', ref='doom-mana-black')
                self.add(session.engine, 'Wastes', ref='doom-mana-generic')
                blink = None
                if transition == 'blink':
                    blink = self.add(session.engine, 'Flicker of Fate', seat='B', zone='hand')
                    self.add(session.engine, 'Plains', seat='B', ref='flicker-white')
                    self.add(session.engine, 'Wastes', seat='B', ref='flicker-generic')
                self.assert_closed(session, damage)
                self.assert_closed(session, salve)
                self.prepare(session, mana={'R': 1})
                self.checkpoint(session)
                identity = creature.logical_object_id
                self.cast_for(session, 'A', damage, [creature.ref])
                self.prevent(session, 'B', salve, creature)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(0, session.state.cards[creature.object_id].marked_damage)
                rules = [rule for rule in session.state.continuous_effects if isinstance(rule, ResolvedDamageDeathExile)]
                self.assertEqual(int(name == 'Magma Spray'), len(rules))
                if rules:
                    self.assertEqual(identity, rules[0].locked_objects[0].logical_object_id)
                if blink is not None:
                    self.cast_for(session, 'B', blink, [creature.ref])
                    self.assertIsNone(self.resolve(session))
                    self.assertNotEqual(identity, session.state.cards[creature.object_id].logical_object_id)
                if transition == 'cleanup':
                    self.finish_turn(session)
                    self.assertFalse(any(isinstance(rule, ResolvedDamageDeathExile) for rule in session.state.continuous_effects))

                session = self.replay(session, load=True)
                creature = session.state.cards[creature.object_id]
                self.cast_for(session, 'A', kill, [creature.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(destination, session.state.cards[creature.object_id].zone)
                self.replay(session)

    def test_original_anger_marks_only_actual_creatures_before_simultaneous_sba(self):
        session = self.session(705_614_609)
        anger = self.add(session.engine, 'Anger of the Gods', zone='hand')
        lethal = self.add(session.engine, 'Grizzly Bears', seat='B', ref='lethal')
        saved = self.add(session.engine, 'Grizzly Bears', seat='C', ref='prevented')
        survivor = self.add(session.engine, 'Wall of Omens', seat='D', ref='dealt-survivor')
        salve = self.add(session.engine, 'Healing Salve', seat='C', zone='hand')
        kill_saved = self.add(session.engine, 'Murder', zone='hand')
        kill_survivor = self.add(session.engine, 'Doom Blade', zone='hand')
        self.add(session.engine, 'Plains', seat='C')
        private = session.state.cards[session.state.players['D'].zones['hand'][0]].ref
        self.assert_closed(session, anger)
        self.prepare(session, mana={'C': 3, 'R': 2, 'B': 3})
        self.checkpoint(session)
        self.cast_for(session, 'A', anger)
        self.prevent(session, 'C', salve, saved)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[lethal.object_id].zone)
        self.assertEqual(0, session.state.cards[saved.object_id].marked_damage)
        self.assertEqual(3, session.state.cards[survivor.object_id].marked_damage)
        rule, = [rule for rule in session.state.continuous_effects if isinstance(rule, ResolvedDamageDeathExile)]
        self.assertEqual({lethal.object_id, survivor.object_id}, {subject.object_id for subject in rule.locked_objects})

        self.cast_for(session, 'A', kill_saved, [saved.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[saved.object_id].zone)
        self.priority(session, 'A')
        action = self.action(session, 'A', kill_survivor)
        command = {'action_id': action['id'], 'targets': [survivor.ref], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.assertTrue(session.act('pilot:A', command).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[survivor.object_id].zone)
        for seat in 'ABC':
            self.assertNotIn(private, json.dumps(session.packet('pilot:' + seat, full=True)))
        self.replay(session)

    def test_original_smite_removes_indestructible_before_the_lethal_check(self):
        session = self.session(705_614_612)
        smite = self.add(session.engine, 'Smite the Deathless', zone='hand')
        creature = self.add(session.engine, 'Darksteel Myr', seat='B')
        self.assert_closed(session, smite)
        self.prepare(session, mana={'C': 1, 'R': 1})
        self.assertIn('indestructible', session.engine._combat_keywords(creature))
        self.checkpoint(session)
        self.cast_for(session, 'A', smite, [creature.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('exile', session.state.cards[creature.object_id].zone)
        self.replay(session)

    def test_original_betrayers_bargain_resolves_with_either_offered_price(self):
        for sacrifice in (False, True):
            with self.subTest(sacrifice=sacrifice):
                session = self.session(705_614_615 + sacrifice)
                spell = self.add(session.engine, "Betrayer's Bargain", zone='hand')
                payer = self.add(session.engine, 'Grizzly Bears')
                target = self.add(session.engine, 'Grizzly Bears', seat='B', ref='bargain-target')
                self.assert_closed(session, spell)
                self.prepare(session, mana={'C': 3, 'R': 1})
                self.checkpoint(session)
                action = self.action(session, 'A', spell)
                options = action['cost_options']
                self.assertEqual(2, len(options))
                selected = next(option for option in options
                                if ('sacrifice_cards' in option.get('choice_schema', {})) is sacrifice)
                command = {'action_id': action['id'], 'cost_option': selected['id'],
                           'targets': [target.ref], 'pay': 'auto'}
                if sacrifice:
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:A', command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    command['sacrifice_cards'] = [payer.ref]
                result = session.act('pilot:A', command)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual('graveyard' if sacrifice else 'battlefield', session.state.cards[payer.object_id].zone)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('exile', session.state.cards[target.object_id].zone)
                self.replay(session)
