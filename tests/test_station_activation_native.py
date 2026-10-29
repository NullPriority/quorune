"""Original threshold activations use actual land/cast/Station commands."""
from pathlib import Path
import tempfile
import unittest
from common import ROOT
import station_symbol_fixture as fixtures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class StationActivationNativeTests(fixtures.StationSymbolFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'station-activations.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/station-activation-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Station activations', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def rows(self, s, source):
        self.priority(s, 'A')
        return [a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':')]

    def submit(self, s, command):
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        r = s.act('pilot:A', command)
        self.assertTrue(r.ok, r.summary)

    def play(self, s, source):
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'] == 'play-land:' + source.ref)
        self.submit(s, {'action_id': offered['id']})
        self.assertTrue(s.state.cards[source.object_id].tapped)

    def test_actual_elevator_preserves_unconditional_mana_and_threshold_color_output(self):
        s = self.session(721662100)
        source = self.add(s.engine, 'The Eternity Elevator', zone='hand')
        workers = [self.add(s.engine, 'Gigantosaurus', ref='elevator-worker-' + str(i)) for i in range(2)]
        twiddle = self.add(s.engine, 'Twiddle', zone='hand')
        self.assert_closed(s, source)
        self.ready(s, {'C': 5, 'U': 1})
        self.cast_for(s, 'A', source, reserve={'U': 1})
        self.settle(s)
        before_rows = self.rows(s, source)
        self.assertFalse(any(a['id'].endswith(':ab3') for a in before_rows))
        normal = next(a for a in before_rows if a['id'].endswith(':ab1'))
        self.submit(s, {'action_id': normal['id']})
        self.assertEqual(3, s.state.players['A'].mana_pool.get('C', 0))
        self.assertEqual([], s.state.stack)
        self.cast(s, 'A', twiddle, [source.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose(s, choice='untap')
        self.settle(s)
        self.station(s, source, workers[0])
        self.assertEqual(10, s.state.cards[source.object_id].counters['charge'])
        self.assertFalse(any(a['id'].endswith(':ab3') for a in self.rows(s, source)))
        self.station(s, source, workers[1])
        self.assertEqual(20, s.state.cards[source.object_id].counters['charge'])
        dynamic = next(a for a in self.rows(s, source) if a['id'].endswith(':ab3'))
        self.assertEqual(20, dynamic['choice_schema']['mana_output']['total'])
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': dynamic['id'], 'mana_output': {'R': 19}}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        self.submit(s, {'action_id': dynamic['id'], 'mana_choice': 'R'})
        self.assertEqual(20, s.state.players['A'].mana_pool.get('R', 0))
        self.assertEqual(3, s.state.players['A'].mana_pool.get('C', 0))
        self.assertEqual([], s.state.stack)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertNotIn('creature', s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'].casefold())
        self.assertIsNone(s.engine._effective_card_data(s.state.cards[source.object_id])['power'])
        self.private(s)
        self.replay(s, load=True)

    def test_actual_ev_end_o_and_uthros_pay_input_then_produce_current_public_quantity(self):
        for index, (name, color) in enumerate((('Evendo, Waking Haven', 'G'), ('Uthros, Titanic Godcore', 'U'))):
            with self.subTest(card=name):
                s = self.session(721662200 + index)
                source = self.add(s.engine, name, zone='hand')
                workers = [self.add(s.engine, 'Gigantosaurus', ref='planet-worker-' + str(i)) for i in range(2)]
                artifact = self.add(s.engine, 'Ornithopter')
                self.add(s.engine, 'Ornithopter', seat='B', ref='foreign-quantity-artifact')
                twiddle = self.add(s.engine, 'Twiddle', zone='hand')
                self.assert_closed(s, source)
                mana = {'U': 1, color: 1} if color != 'U' else {'U': 2}
                self.ready(s, mana)
                self.play(s, source)
                self.cast_for(s, 'A', twiddle, [source.ref], reserve={color: 1})
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.choose(s, choice='untap')
                self.settle(s)
                self.assertFalse(any(a['id'].endswith(':ab4') for a in self.rows(s, source)))
                for worker in workers:
                    self.station(s, source, worker)
                action = next(a for a in self.rows(s, source) if a['id'].endswith(':ab4'))
                expected = 3 if color == 'G' else 1
                self.assertTrue(action.get('mana_ability'))
                before = s.state.players['A'].mana_pool.get(color, 0)
                s = self.replay(s, load=True)
                self.submit(s, {'action_id': action['id'], 'pay': 'auto'})
                self.assertEqual(before - 1 + expected, s.state.players['A'].mana_pool.get(color, 0))
                self.assertEqual([], s.state.stack)
                self.assertTrue(s.state.cards[source.object_id].tapped)
                self.assertNotIn('creature', s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'].casefold())
                self.assertEqual('battlefield', s.state.cards[artifact.object_id].zone)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_kavaron_sacrifices_a_land_before_stacked_robot_and_haste_result(self):
        s = self.session(721662300)
        source = self.add(s.engine, 'Kavaron, Memorial World', zone='hand')
        workers = [self.add(s.engine, 'Gigantosaurus', ref='kavaron-worker-' + str(i)) for i in range(2)]
        land = self.add(s.engine, 'Plains')
        twiddle = self.add(s.engine, 'Twiddle', zone='hand')
        self.assert_closed(s, source)
        self.ready(s, {'C': 1, 'R': 1, 'U': 1})
        self.play(s, source)
        self.cast_for(s, 'A', twiddle, [source.ref], reserve={'C': 1, 'R': 1})
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose(s, choice='untap')
        self.settle(s)
        for worker in workers:
            self.station(s, source, worker)
        action = next(a for a in self.rows(s, source) if a['id'].endswith(':ab4'))
        self.submit(s, {'action_id': action['id'], 'cost_cards': [land.ref], 'pay': 'auto'})
        self.assertEqual('graveyard', s.state.cards[land.object_id].zone)
        self.assertTrue(s.state.stack)
        self.assertFalse(any(c.is_token for c in s.state.cards.values()))
        s = self.replay(s, load=True)
        self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        robot = tokens[0]
        self.assertEqual((3, 2), tuple(s.engine._numeric_stat(robot.object_id, stat) for stat in ('power', 'toughness')))
        self.assertIn('robot', s.engine._effective_card_data(robot)['type_line'].casefold())
        self.assertIn('haste', s.engine._combat_keywords(robot))
        self.assertEqual(11, s.engine._numeric_stat(workers[0].object_id, 'power'))
        self.assertNotIn('creature', s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'].casefold())
        self.finish_turn(s)
        self.assertEqual((2, 2), tuple(s.engine._numeric_stat(robot.object_id, stat) for stat in ('power', 'toughness')))
        self.assertNotIn('haste', s.engine._combat_keywords(s.state.cards[robot.object_id]))
        self.assertEqual(10, s.engine._numeric_stat(workers[0].object_id, 'power'))
        self.private(s)
        self.replay(s, load=True)
