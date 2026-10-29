"""Original self-label clauses compose with Crew, Station and live queries."""
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


class SourceLabelNativeTests(fixtures.StationSymbolFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'source-labels.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/source-label-characteristic-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source labels', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def activate_id(self, s, source, suffix, **fields):
        self.priority(s, 'A')
        identifier = 'activate:' + source.ref + ':' + suffix
        action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'] == identifier)
        command = {'action_id': action['id'], **fields}
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        r = s.act('pilot:A', command)
        self.assertTrue(r.ok, r.summary)

    def test_actual_vibranium_mech_stat_activation_expires_after_crew_attack_and_two_tokens(self):
        s = self.session(201663100)
        source = self.add(s.engine, 'Vibranium Mining Mech', zone='hand')
        worker = self.add(s.engine, 'Grizzly Bears')
        untap = self.add(s.engine, 'Twiddle', zone='hand')
        self.add(s.engine, 'Island')
        self.add(s.engine, 'Fervor')
        self.assert_closed(s, source)
        self.ready(s, {'C': 6})
        self.cast_for(s, 'A', source, reserve={'C': 2})
        self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(1, len(tokens))
        self.assertTrue(tokens[0].tapped)
        self.assertIn('indestructible', s.engine._combat_keywords(tokens[0]))
        crew = next(a for a in s.engine._activated_abilities(s.state.cards[source.object_id]) if a.crew_threshold == 2)
        self.activate_id(s, source, crew.ability_id, cost_cards=[worker.ref])
        self.settle(s)
        self.assertEqual((6, 6), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.activate_id(s, source, 'ab3', pay='auto')
        s = self.replay(s, load=True)
        self.settle(s)
        self.assertEqual((7, 6), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.attack(s, [(source, 'B')])
        self.assertEqual(1, self.trigger_count(s, source))
        self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(c.tapped for c in tokens))
        self.until(s, lambda s: s.state.players['B'].life == 33)
        self.finish_turn(s)
        self.assertNotIn('creature', s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'].casefold())
        self.cast(s, 'A', untap, [worker.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose(s, choice='untap')
        self.settle(s)
        self.activate_id(s, source, crew.ability_id, cost_cards=[worker.ref])
        self.settle(s)
        self.assertEqual(6, s.engine._numeric_stat(source.object_id, 'power'))
        self.private(s)
        self.replay(s, load=True)

    def test_actual_debris_crusher_entry_damage_stat_growth_and_new_incarnation_are_distinct(self):
        s = self.session(201663200)
        source = self.add(s.engine, 'Debris Field Crusher', zone='hand')
        worker = self.add(s.engine, 'Gigantosaurus')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B')
        self.add(s.engine, 'Fervor')
        self.assert_closed(s, source)
        self.ready(s, {'C': 6, 'R': 3})
        self.cast_for(s, 'A', source, reserve={'C': 2, 'R': 2})
        self.target(s, 'A', ['C'])
        self.settle(s)
        self.assertEqual(37, s.state.players['C'].life)
        self.animate(s, source, [worker], 8, (1, 5))
        self.activate_id(s, source, 'ab4', pay='auto')
        self.settle(s)
        self.assertEqual((3, 5), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        original_identity = s.state.cards[source.object_id].logical_object_id
        self.activate_id(s, source, 'ab4', pay='auto')
        self.cast(s, 'B', bounce, [source.ref])
        s = self.replay(s, load=True)
        self.settle(s)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertNotEqual(original_identity, s.state.cards[source.object_id].logical_object_id)
        self.assertEqual({}, s.state.cards[source.object_id].counters)
        self.assertIsNone(s.engine._effective_card_data(s.state.cards[source.object_id])['power'])
        self.private(s)
        self.replay(s, load=True)

    def test_actual_research_craft_live_artifact_count_and_threshold_cast_trigger_compose(self):
        s = self.session(201663300)
        source = self.add(s.engine, 'Uthros Research Craft', zone='hand')
        workers = [self.add(s.engine, 'Gigantosaurus', ref='research-worker-' + str(i)) for i in range(2)]
        first = self.add(s.engine, 'Ornithopter', zone='hand', ref='first-research-artifact')
        second = self.add(s.engine, 'Ornithopter', zone='hand', ref='second-research-artifact')
        self.add(s.engine, 'Ornithopter', seat='B', ref='foreign-research-artifact')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B')
        self.assert_closed(s, source)
        self.ready(s, {'C': 2, 'U': 1})
        self.cast(s, 'A', source)
        self.settle(s)
        hand = len(s.state.players['A'].zones['hand'])
        self.cast(s, 'A', first)
        self.assertEqual(0, self.trigger_count(s, source))
        self.settle(s)
        self.assertEqual(hand - 1, len(s.state.players['A'].zones['hand']))
        self.station(s, source, workers[0])
        self.assertEqual(10, s.state.cards[source.object_id].counters['charge'])
        self.cast(s, 'A', second)
        self.assertEqual(1, self.trigger_count(s, source))
        self.settle(s)
        self.assertEqual(11, s.state.cards[source.object_id].counters['charge'])
        self.assertEqual(hand - 1, len(s.state.players['A'].zones['hand']))
        self.station(s, source, workers[1])
        self.assertEqual((3, 8), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.cast(s, 'B', bounce, [first.ref])
        self.settle(s)
        self.assertEqual((2, 8), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.private(s)
        self.replay(s, load=True)
