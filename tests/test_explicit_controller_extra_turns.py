"""An explicit you retains the resolving ability controller without targeting."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.compiler.extra_turn_templates import fixed_extra_turn_effect_template
from scripts.build_test_database import build_fixture_database


class ExplicitControllerExtraTurnCompilerTests(unittest.TestCase):
    def test_you_is_the_untargeted_controller_for_each_fixed_count(self):
        for quantity, count in [('an', 1), ('two', 2), ('ten', 10)]:
            ending = ' extra turn'+('s' if count != 1 else '')+' after this one.'
            with self.subTest(count=count):
                imperative = fixed_extra_turn_effect_template('Take '+quantity+ending)
                explicit = fixed_extra_turn_effect_template('You take '+quantity+ending)
                self.assertEqual(imperative, explicit)
                self.assertIsNotNone(explicit)
                self.assertIsNone(explicit[2])
                self.assertEqual(count, len(explicit[1]))
                self.assertTrue(all(effect['player'] == '$controller' for effect in explicit[1]))

    def test_old_targeted_recipients_keep_their_existing_target_contract(self):
        for subject, relation in [('Target player', 'any'), ('Target opponent', 'opponent')]:
            with self.subTest(subject=subject):
                result = fixed_extra_turn_effect_template(subject+' takes an extra turn after this one.')
                self.assertEqual('$target.0', result[1][0]['player'])
                self.assertEqual(relation, result[2]['player_relation'])

    def test_ambiguous_dynamic_conditional_and_changed_timing_clauses_remain_unowned(self):
        for text in ('That player takes an extra turn after this one.',
                     'You take X extra turns after this one.',
                     'You take an extra turn after your next turn.',
                     'You take an extra turn after this one if you control a creature.',
                     'You take two extra turn after this one.',
                     'You take zero extra turns after this one.'):
            with self.subTest(text=text):
                self.assertIsNone(fixed_extra_turn_effect_template(text))


class ExplicitControllerExtraTurnNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish
    finish_turn = seat_actions.RecipientControlNativeTests.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'explicit-controller-turns.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/explicit-controller-extra-turn-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Explicit controller turns', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def world(self, seed, *, donate=False):
        session = self.session(seed)
        source = self.add(session.engine, 'Wormfang Manta', zone='hand')
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        private = self.add(session.engine, 'Counterspell', seat='C', zone='hand')
        gift = None
        if donate:
            gift = self.add(session.engine, 'Donate', zone='hand')
            flash = self.add(session.engine, 'Vedalken Orrery')
            self.assert_closed(session, flash)
            self.assert_closed(session, gift)
        for card in (source, bounce):self.assert_closed(session, card)
        self.prepare(session, mana={'C': 10, 'U': 6})
        self.checkpoint(session)
        return session, source, bounce, private, gift

    def test_whole_original_admits_entry_skip_and_untargeted_departure_extra_turn(self):
        session = self.session(500701)
        source = self.add(session.engine, 'Wormfang Manta', zone='hand')
        self.assert_closed(session, source)
        ir = compile_oracle_card(self.db.lookup('Wormfang Manta'),
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        programs = session.engine.semantics.programs_for_oracle(source.oracle_id)
        departure, = [program for program in programs
                       if any(effect.get('op') == 'extra_turn' for effect in program.effects)]
        self.assertIsNone(departure.target_schema)
        self.assertEqual('$controller', departure.effects[0]['player'])

    def test_original_departure_extra_turn_is_consumed_by_its_entry_skip(self):
        session, source, bounce, private, _ = self.world(500710)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.assertFalse(session.state.extra_turns)
        self.cast(session, 'A', bounce, [source.ref])
        self.resolve_top(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['A'], [entry.player for entry in session.state.extra_turns])
        self.finish_turn(session)
        self.assertEqual('B', session.state.active_player)
        self.assertFalse(session.state.extra_turns)
        self.assertFalse(session.state.scheduled_turn_skips)
        self.finish(session, private)

    def test_donation_keeps_pending_skip_player_and_departure_last_controller_distinct(self):
        session, source, bounce, private, gift = self.world(500720, donate=True)
        self.cast(session, 'A', source)
        self.resolve_top(session)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.cast(session, 'A', gift, ['B', source.ref])
        self.resolve_top(session)
        self.assertEqual('B', session.state.cards[source.object_id].controller)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.cast(session, 'A', bounce, [source.ref])
        self.resolve_top(session)
        self.assertEqual('A', session.state.cards[source.object_id].owner)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(['B'], [entry.player for entry in session.state.extra_turns])
        self.finish_turn(session)
        self.assertEqual('B', session.state.active_player)
        self.assertEqual(['A'], [grant.player for grant in session.state.scheduled_turn_skips])
        self.finish(session, private)

    def test_fresh_extra_turn_commit_omission_is_killed_by_original_actions(self):
        method = 'test_original_departure_extra_turn_is_consumed_by_its_entry_skip'
        import quorune.effect_runtime.damage_life_and_turns as owner
        with patch.dict(owner.HANDLERS, {'extra_turn': lambda *args, **kwargs: None}):
            with self.assertRaises(AssertionError):getattr(type(self)(method), method)()
