"""Original named-source combat results compose with existing rules owners."""
from pathlib import Path
import tempfile
import unittest
from common import ROOT
import optional_result_followup_fixture as fixtures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class NamedSourceResultTests(fixtures.OptionalResultFollowupFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'named-source-results.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/named-source-result-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Named source results', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def test_actual_zuko_noncreature_cast_firebending_and_first_strike_damage_keep_source_identity(self):
        s = self.session(613659001)
        source = self.add(s.engine, 'Zuko, Seeking Honor', zone='hand')
        growth = self.add(s.engine, 'Giant Growth', zone='hand')
        shock = self.add(s.engine, 'Shock', zone='hand', ref='zuko-own-shock')
        foreign = self.add(s.engine, 'Shock', seat='B', zone='hand', ref='zuko-foreign-shock')
        self.add(s.engine, 'Fervor')
        self.add(s.engine, 'Divination', zone='hand', ref='private-recipient-hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2, 'B': 1, 'G': 1})
        s.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(s)
        self.cast_for(s, 'A', source, reserve={'G': 1})
        self.settle(s)
        self.assertNotIn('first strike', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.cast(s, 'B', foreign, ['D'])
        self.assertEqual(0, self.trigger_count(s, source))
        self.settle(s)
        self.assertNotIn('first strike', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.cast(s, 'A', growth, [source.ref])
        self.assertEqual(1, self.trigger_count(s, source))
        self.settle(s)
        self.assertEqual(5, s.engine._numeric_stat(source.object_id, 'power'))
        self.assertIn('first strike', s.engine._combat_keywords(s.state.cards[source.object_id]))
        life = {seat: s.state.players[seat].life for seat in 'BC'}
        self.attack(s, [(source, 'B')])
        self.assertEqual(1, self.trigger_count(s, source))
        self.settle(s)
        self.assertEqual(1, s.state.players['A'].mana_pool.get('R', 0))
        self.until(s, lambda s: s.state.step == 'declare_blockers' and s.state.pending_decision is not None
                   and s.state.pending_decision.kind == 'priority')
        self.assertEqual(1, s.state.players['A'].mana_pool.get('R', 0))
        self.cast(s, 'A', shock, ['C'])
        self.settle(s)
        self.assertEqual(life['C'] - 2, s.state.players['C'].life)
        self.assertEqual(0, s.state.players['A'].mana_pool.get('R', 0))
        self.until(s, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual(life['B'] - 5, s.state.players['B'].life)
        self.settle(s)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertEqual(6, s.engine._numeric_stat(source.object_id, 'power'))
        self.until(s, lambda s: s.state.phase_index >= 9)
        self.assertEqual(life['B'] - 5, s.state.players['B'].life)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.private(s)
        s = self.replay(s, load=True)
        self.finish_turn(s)
        self.assertEqual((3, 3), tuple(s.engine._numeric_stat(source.object_id, k) for k in ('power', 'toughness')))
        self.assertNotIn('first strike', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.replay(s, load=True)
