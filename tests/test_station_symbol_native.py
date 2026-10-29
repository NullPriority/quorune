"""Original Station cards cross thresholds through real Station activations."""
from pathlib import Path
import tempfile
import unittest
import station_symbol_fixture as station_fixtures
from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.station import station_cost_choice
from scripts.build_test_database import build_fixture_database


class StationSymbolNativeTests(station_fixtures.StationSymbolFixture, unittest.TestCase):


    def test_actual_lumen_cumulative_anthem_animation_counter_loss_and_pending_replay(self):
        s = self.session(721661100)
        source = self.add(s.engine, 'Lumen-Class Frigate', zone='hand')
        first = self.add(s.engine, 'Llanowar Elves', ref='first-charge-worker')
        second = self.add(s.engine, 'Llanowar Elves', ref='second-charge-worker')
        dragon = self.add(s.engine, 'Shivan Dragon')
        bear = self.add(s.engine, 'Grizzly Bears')
        thopter = self.add(s.engine, 'Ornithopter')
        clear = self.add(s.engine, 'Vampire Hexmage')
        self.assert_closed(s, source)
        self.assert_closed(s, clear)
        self.ready(s, {'C': 1, 'W': 1})
        self.assertIsNone(s.engine._effective_card_data(source)['power'])
        self.cast(s, 'A', source)
        self.settle(s)
        self.assertNotIn('creature', str(s.engine._effective_card_data(source)['type_line']).casefold())
        self.assertIsNone(s.engine._effective_card_data(source)['power'])
        self.assertEqual(2, s.engine._numeric_stat(bear.object_id, 'power'))
        self.station(s, source, first)
        self.assertEqual(1, s.state.cards[source.object_id].counters['charge'])
        self.assertEqual(2, s.engine._numeric_stat(bear.object_id, 'power'))
        self.station(s, source, second)
        self.assertEqual(2, s.state.cards[source.object_id].counters['charge'])
        self.assertEqual(3, s.engine._numeric_stat(bear.object_id, 'power'))
        self.station(s, source, dragon)
        self.assertEqual(8, s.state.cards[source.object_id].counters['charge'])
        self.station(s, source, bear)
        self.assertEqual(11, s.state.cards[source.object_id].counters['charge'])
        self.assertNotIn('flying', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertIsNone(s.engine._effective_card_data(source)['power'])
        self.station(s, source, thopter, pending=True)
        s = self.replay(s, load=True)
        self.assertEqual(11, s.state.cards[source.object_id].counters['charge'])
        self.settle(s)
        self.assertEqual(12, s.state.cards[source.object_id].counters['charge'])
        data = s.engine._effective_card_data(s.state.cards[source.object_id])
        self.assertIn('artifact', data['type_line'].casefold())
        self.assertIn('creature', data['type_line'].casefold())
        self.assertIn('spacecraft', data['type_line'].casefold())
        self.assertEqual((3, 5), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.assertTrue({'flying', 'lifelink'} <= s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertEqual(3, s.engine._numeric_stat(bear.object_id, 'power'))
        self.activate(s, 'A', clear, [source.ref])
        self.settle(s)
        self.assertEqual({}, s.state.cards[source.object_id].counters)
        self.assertNotIn('creature', s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'].casefold())
        self.assertIsNone(s.engine._effective_card_data(s.state.cards[source.object_id])['power'])
        self.assertNotIn('flying', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.assertEqual(2, s.engine._numeric_stat(bear.object_id, 'power'))
        self.private(s)
        self.replay(s, load=True)

    def test_actual_sawship_attack_legality_changes_at_exact_station_threshold_on_cast_turn(self):
        for count in (2, 3):
            with self.subTest(charge=count):
                s = self.session(721661200 + count)
                source = self.add(s.engine, 'Galvanizing Sawship', zone='hand')
                worker = self.add(s.engine, 'Grizzly Bears' if count == 2 else 'Centaur Courser')
                other = self.add(s.engine, 'Grizzly Bears', ref='independent-combat-attacker')
                other.acquired_control_turn_count = -1
                self.assert_closed(s, source)
                self.ready(s, {'C': 5, 'R': 1})
                self.cast(s, 'A', source)
                self.settle(s)
                self.station(s, source, worker)
                self.assertEqual(count, s.state.cards[source.object_id].counters['charge'])
                self.assertEqual(count == 3, 'flying' in s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.assertEqual(count == 3, 'haste' in s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
                if count == 2:
                    before = authoritative_state_hash(s.state)
                    self.assertFalse(s.act('pilot:A', {'action_id': 'attack', 'attackers': {source.ref: 'B'}}).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                    self.attack(s, [])
                else:
                    self.attack(s, [(source, 'B')])
                    self.until(s, lambda s: s.state.players['B'].life == 34)
                    self.assertEqual((6, 5), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
                self.private(s)
                self.replay(s, load=True)
