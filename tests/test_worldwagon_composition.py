"""Original source-label definition, optional entry/attack search and Crew."""
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


class WorldwagonCompositionTests(fixtures.StationSymbolFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'worldwagon.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', ROOT / 'tests/fixtures/worldwagon-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Worldwagon composition', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def test_actual_worldwagon_entry_and_attack_search_update_live_power_before_damage(self):
        s = self.session(201663800)
        source = self.add(s.engine, 'Lumbering Worldwagon', zone='hand')
        worker = self.add(s.engine, 'Gigantosaurus')
        self.add(s.engine, 'Fervor')
        self.add(s.engine, 'Plains', ref='worldwagon-existing-land')
        entry_land = self.add(s.engine, 'Island', zone='library', ref='private-worldwagon-entry-land')
        attack_land = self.add(s.engine, 'Forest', zone='library', ref='private-worldwagon-attack-land')
        foreign = self.add(s.engine, 'Island', seat='B', zone='library', ref='foreign-worldwagon-library-land')
        self.assert_closed(s, source)
        self.ready(s, {'C': 2, 'G': 1})
        self.assertEqual('1', s.engine._effective_card_data(source)['power'])
        self.cast(s, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, True)
        self.assertEqual('semantic.search', self.resolve(s).kind)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'search_cards': [foreign.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        shuffles = s.state.players['A'].stats.get('shuffle_count', 0)
        s = self.replay(s, load=True)
        self.choose(s, search_cards=[entry_land.ref])
        self.settle(s)
        self.assertTrue(s.state.cards[entry_land.object_id].tapped)
        self.assertEqual(shuffles + 1, s.state.players['A'].stats.get('shuffle_count', 0))
        crew = next(a for a in s.engine._activated_abilities(s.state.cards[source.object_id]) if a.crew_threshold == 4)
        self.priority(s, 'A')
        identifier = 'activate:' + source.ref + ':' + crew.ability_id
        self.assertTrue(any(a['id'] == identifier for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']))
        before = authoritative_state_hash(s.state)
        command = {'action_id': identifier, 'cost_cards': [worker.ref]}
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.settle(s)
        self.assertEqual((2, 4), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.attack(s, [(source, 'B')])
        self.assertEqual(1, self.trigger_count(s, source))
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, True)
        self.assertEqual('semantic.search', self.resolve(s).kind)
        self.choose(s, search_cards=[attack_land.ref])
        self.settle(s)
        self.assertTrue(s.state.cards[attack_land.object_id].tapped)
        self.assertEqual(shuffles + 2, s.state.players['A'].stats.get('shuffle_count', 0))
        self.assertEqual((3, 4), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
        self.until(s, lambda s: s.state.players['B'].life == 37)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_worldwagon_declined_entry_search_keeps_library_and_shuffle_count(self):
        s = self.session(201663801)
        source = self.add(s.engine, 'Lumbering Worldwagon', zone='hand')
        self.assert_closed(s, source)
        self.ready(s, {'C': 2, 'G': 1})
        count = len(s.state.players['A'].zones['library'])
        shuffles = s.state.players['A'].stats.get('shuffle_count', 0)
        self.cast(s, 'A', source)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, False)
        self.settle(s)
        self.assertEqual(count, len(s.state.players['A'].zones['library']))
        self.assertEqual(shuffles, s.state.players['A'].stats.get('shuffle_count', 0))
        self.private(s)
        self.replay(s, load=True)
