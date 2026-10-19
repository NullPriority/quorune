"""Pinned CR 303.4, 608.2b and 702.5: live fixed Enchant restrictions."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.aura import parse_enchant_line, TypedEnchantSpec
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_scalar_mill_wording as controls


class FixedCharacteristicEnchantCompilerTests(unittest.TestCase):
    def test_fixed_restrictions_use_existing_target_predicates_and_closed_version(self):
        for text, field, expected in (
            ('Enchant creature without flying', 'keywords_none', ['flying']),
            ('Enchant creature with power 3 or less', 'numeric_characteristic',
                {'characteristic':'power', 'comparison':'at_most', 'value':3}),
            ('Enchant creature with mana value 2 or less', 'mana_value_max', 2),
        ):
            with self.subTest(text=text):
                spec = parse_enchant_line(text)
                self.assertIsNotNone(spec)
                self.assertEqual(3, spec.schema_version)
                self.assertEqual(expected, spec.target_schema()[field])
                if spec.numeric_characteristic is not None:
                    self.assertTrue(spec.numeric_characteristic.permits(power=3, toughness=2))
                    self.assertFalse(spec.numeric_characteristic.permits(power=4, toughness=2))
                self.assertEqual(spec, TypedEnchantSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
                legacy = parse_enchant_line('Enchant blue creature')
                self.assertEqual(2, legacy.schema_version)
                self.assertNotIn(field, legacy.to_dict())
                self.assertEqual(legacy, TypedEnchantSpec.from_dict(legacy.to_dict()))
                self.assertEqual('47c6810b12af136853e5b62e6afdc2494573b0cf2a46146ae98025f5e4432aea',
                    legacy.fingerprint)
                for changed in (
                    {**spec.to_dict(), 'schema_version':2},
                    {**spec.to_dict(), 'unexpected':True},
                    {**spec.to_dict(), 'subject':'player'},
                    {**spec.to_dict(), 'numeric_characteristic':{'characteristic':'power','comparison':'at_most','value':True}},
                    {**spec.to_dict(), 'mana_value_max':True},
                    {**spec.to_dict(), 'keywords_none':['flying','unowned ability']},
                    {**spec.to_dict(), 'keywords_none':None},
                ):
                    with self.assertRaises(ValueError):
                        TypedEnchantSpec.from_dict(changed)

    def test_nearby_unsupported_and_ambiguous_restrictions_stay_residual(self):
        for text in ('Enchant creature with another Aura attached to it',
                'Enchant modified creature', 'Enchant nonbasic land',
                'Enchant creature with power X or less',
                'Enchant creature with power less than this creature',
                'Enchant creature without flying or vigilance',
                'Enchant creature with power 3 or less and toughness 4 or greater'):
            with self.subTest(text=text):
                self.assertIsNone(parse_enchant_line(text))


class FixedCharacteristicEnchantRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ScalarMillWordingRuntimeTests.session
    add = controls.ScalarMillWordingRuntimeTests.add
    prepare = controls.ScalarMillWordingRuntimeTests.prepare
    cast = controls.ScalarMillWordingRuntimeTests.cast
    checkpoint = controls.ScalarMillWordingRuntimeTests.checkpoint
    resolve = controls.ScalarMillWordingRuntimeTests.resolve
    replay = controls.ScalarMillWordingRuntimeTests.replay
    until = controls.ScalarMillWordingRuntimeTests.until
    resolve_spell = controls.ScalarMillWordingRuntimeTests.resolve_spell
    binding = controls.ScalarMillWordingRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'fixed-enchant.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-characteristic-enchant-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Fixed Enchant', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def fixture(self, name, seed):
        session = self.session(seed)
        aura = self.add(session.engine, name, zone='hand')
        bear = self.add(session.engine, 'Grizzly Bears', seat='B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U':5,'W':4,'G':4,'C':8})
        return session, aura, bear, private

    def check_private(self, session, private):
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+viewer, full=True)))

    def test_actual_power_restriction_rechecks_resolution_and_attached_state(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                session, aura, bear, private = self.fixture("Runner's Bane", 71013001+pending)
                if not pending:
                    bear = self.add(session.engine, 'Centaur Courser', seat='B')
                initial_power = 2 if pending else 3
                growth = self.add(session.engine, 'Giant Growth', zone='hand')
                self.prepare(session, mana={'U':5,'G':4,'C':8})
                self.checkpoint(session)
                self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
                command = {'action_id':'cast:'+aura.ref, 'targets':[bear.ref], 'pay':'auto'}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:D', command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.cast(session, 'A', aura, [bear.ref])
                if not pending:
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(bear.object_id, session.state.cards[aura.object_id].attached_to)
                    self.assertTrue(session.state.cards[bear.object_id].tapped)
                self.cast(session, 'A', growth, [bear.ref])
                self.until(session, lambda s:s.engine._numeric_stat(bear.object_id,'power') == initial_power + 3)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
                self.assertEqual('B', session.state.cards[bear.object_id].controller)
                self.assertEqual(not pending, session.state.cards[bear.object_id].tapped)
                self.check_private(session, private)
                self.replay(session, load=True)

    def test_actual_nonflying_restriction_uses_current_keyword_before_and_after_attachment(self):
        for name, pending in (('Roots',True), ('Trapped in the Tower',False)):
            with self.subTest(name=name, pending=pending):
                session, aura, bear, private = self.fixture(name, 71013010+pending)
                jump = self.add(session.engine, 'Jump', zone='hand')
                self.prepare(session, mana={'U':5,'W':4,'G':4,'C':8})
                self.checkpoint(session)
                self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
                self.cast(session, 'A', aura, [bear.ref])
                if not pending:
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual(bear.object_id, session.state.cards[aura.object_id].attached_to)
                self.cast(session, 'A', jump, [bear.ref])
                self.until(session, lambda s:s.state.cards[jump.object_id].zone == 'graveyard')
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
                self.assertEqual('battlefield', session.state.cards[bear.object_id].zone)
                self.check_private(session, private)
                self.replay(session, load=True)

    def test_actual_mana_value_restriction_changes_control_but_rejects_three_and_new_incarnations(self):
        session, aura, bear, private = self.fixture('Threads of Disloyalty',71013020)
        courser = self.add(session.engine, 'Centaur Courser', seat='B')
        bounce = self.add(session.engine, 'Unsummon', zone='hand')
        self.prepare(session, mana={'U':5,'C':8})
        self.checkpoint(session)
        self.assertTrue(self.binding(session, aura)['strict_capability_ready'])
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:A',{'action_id':'cast:'+aura.ref,'targets':[courser.ref],'pay':'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', aura, [bear.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('A', session.state.cards[bear.object_id].controller)
        self.assertEqual(bear.object_id, session.state.cards[aura.object_id].attached_to)
        identity = session.state.cards[bear.object_id].logical_object_id
        self.cast(session, 'A', bounce, [bear.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        self.assertEqual('hand', session.state.cards[bear.object_id].zone)
        self.assertEqual('B', session.state.cards[bear.object_id].owner)
        self.assertNotEqual(identity, session.state.cards[bear.object_id].logical_object_id)
        self.check_private(session, private)
        self.replay(session, load=True)

    def test_fixed_restriction_lowering_omission_is_killed_by_native_gameplay(self):
        method = 'test_actual_mana_value_restriction_changes_control_but_rejects_three_and_new_incarnations'
        with patch('quorune.aura.typed_grammar.parse_typed_enchant_line', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_nonspell_entry_uses_untargeted_restriction_choice_despite_shroud(self):
        session = self.session(71013030)
        hand_aura = self.add(session.engine, 'Roots', zone='hand', ref='fixed-enchant-hand-roots')
        aura = self.add(session.engine, 'Roots', zone='graveyard', ref='fixed-enchant-grave-roots')
        entrant = self.add(session.engine, 'Generic Fixed Enchant Return', zone='hand')
        insect = self.add(session.engine, 'Deadly Insect', seat='B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U':5,'G':4,'C':8})
        self.checkpoint(session)
        for card in (aura, entrant, insect):
            self.assertTrue(self.binding(session, card)['strict_capability_ready'])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'cast:'+hand_aura.ref,
            'targets':[insect.ref],'pay':'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', entrant, [aura.ref])
        self.assertEqual('aura.entry', self.resolve(session).kind)
        self.assertEqual('graveyard', session.state.cards[aura.object_id].zone)
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        command = {'action_id':'choose','aura_target':insect.ref}
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        chosen = session.act('pilot:A',command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[aura.object_id].zone)
        self.assertEqual(insect.object_id, session.state.cards[aura.object_id].attached_to)
        self.assertTrue(session.state.cards[insect.object_id].tapped)
        self.assertEqual('B', session.state.cards[insect.object_id].controller)
        self.check_private(session, private)
        self.replay(session, load=True)
