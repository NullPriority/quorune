"""Original token observers use ordinary native spells and APNAP stacking."""
from pathlib import Path
import tempfile
import unittest
from common import ROOT
import optional_result_followup_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SingularTokenEntryNativeTests(actions.OptionalResultFollowupFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'singular-token-entry.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
                               ROOT / 'tests/fixtures/singular-token-entry-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Singular token entry', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
                                 DeckEntry('Generic Bound Plains', 30)], ['Isamaru, Hound of Konda'])

    def test_actual_wildwood_and_swarmkeeper_distinguish_simultaneous_noncreature_and_foreign_tokens_then_attack(self):
        s = self.session(111660100)
        source = self.add(s.engine, 'Wildwood Mentor', zone='hand')
        batch = self.add(s.engine, 'Cloakwood Swarmkeeper', zone='hand')
        alarm = self.add(s.engine, 'Raise the Alarm', zone='hand')
        rich = self.add(s.engine, 'Strike It Rich', zone='hand')
        foreign = self.add(s.engine, 'Raise the Alarm', seat='B', zone='hand', ref='foreign-token-spell')
        self.add(s.engine, 'Fervor')
        self.assert_closed(s, source)
        self.assert_closed(s, batch)
        self.ready(s, {'C': 5, 'G': 3, 'W': 1, 'R': 1})
        s.state.players['B'].mana_pool.update(C=1, W=1)
        self.checkpoint(s)
        self.cast(s, 'A', source)
        self.settle(s)
        self.cast(s, 'A', batch)
        self.settle(s)
        self.assertEqual({}, s.state.cards[source.object_id].counters)
        self.cast(s, 'B', foreign)
        self.settle(s)
        self.assertEqual({}, s.state.cards[source.object_id].counters)
        self.assertEqual({}, s.state.cards[batch.object_id].counters)
        self.cast(s, 'A', alarm)
        self.until(s, lambda s: self.trigger_count(s, source) == 2 and self.trigger_count(s, batch) == 1)
        s = self.replay(s, load=True)
        self.settle(s)
        self.assertEqual(2, s.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(1, s.state.cards[batch.object_id].counters['+1/+1'])
        self.cast(s, 'A', rich)
        self.until(s, lambda s: self.trigger_count(s, source) == 1 and self.trigger_count(s, batch) == 1)
        self.settle(s)
        self.assertEqual(3, s.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(2, s.state.cards[batch.object_id].counters['+1/+1'])
        soldiers = [c for c in s.state.cards.values() if c.is_token and c.controller == 'A' and c.zone == 'battlefield'
                    and 'creature' in str(s.engine._effective_card_data(c)['type_line']).casefold()]
        self.assertEqual(2, len(soldiers))
        self.attack(s, [(source, 'B'), (soldiers[0], 'C'), (soldiers[1], 'D')])
        self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'semantic.target')
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'targets': [source.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.target(s, 'A', [soldiers[0].ref])
        self.settle(s)
        self.assertEqual(4, s.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual((5, 5), tuple(s.engine._numeric_stat(soldiers[0].object_id, stat) for stat in ('power', 'toughness')))
        self.assertEqual((1, 1), tuple(s.engine._numeric_stat(soldiers[1].object_id, stat) for stat in ('power', 'toughness')))
        self.private(s)
        self.replay(s, load=True)

    def test_actual_junk_winder_affinity_two_token_targets_survive_source_bounce_and_skip_each_controller_untap(self):
        s = self.session(111660200)
        source = self.add(s.engine, 'Junk Winder', zone='hand')
        early = self.add(s.engine, 'Raise the Alarm', zone='hand', ref='affinity-token-spell')
        later = self.add(s.engine, 'Raise the Alarm', zone='hand', ref='trigger-token-spell')
        first = self.add(s.engine, 'Grizzly Bears', seat='B', ref='token-tap-b')
        second = self.add(s.engine, 'Grizzly Bears', seat='C', ref='token-tap-c')
        own = self.add(s.engine, 'Grizzly Bears')
        land = self.add(s.engine, 'Island', seat='C')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B', ref='bounce-island-b')
        self.assert_closed(s, source)
        self.ready(s, {'C': 5, 'U': 2, 'W': 2})
        self.cast_for(s, 'A', early, reserve={'C': 4, 'U': 2, 'W': 1})
        self.settle(s)
        self.cast_for(s, 'A', source, reserve={'C': 1, 'W': 1})
        self.settle(s)
        self.assertEqual(2, sum(c.is_token and c.zone == 'battlefield' and c.controller == 'A' for c in s.state.cards.values()))
        self.assertEqual(1, s.state.players['A'].mana_pool.get('C', 0))
        self.cast(s, 'A', later)
        self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'semantic.target')
        for illegal in (own, land):
            before = authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'targets': [illegal.ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        self.target(s, 'A', [first.ref])
        self.target(s, 'A', [second.ref])
        self.assertEqual(2, self.trigger_count(s, source))
        self.cast(s, 'B', bounce, [source.ref])
        s = self.replay(s, load=True)
        self.settle(s)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertTrue(s.state.cards[first.object_id].tapped)
        self.assertTrue(s.state.cards[second.object_id].tapped)
        self.finish_turn(s)
        self.assertEqual('B', s.state.active_player)
        self.assertTrue(s.state.cards[first.object_id].tapped)
        self.finish_turn(s)
        self.assertEqual('C', s.state.active_player)
        self.assertTrue(s.state.cards[second.object_id].tapped)
        for _ in range(3):
            self.finish_turn(s)
        self.assertEqual('B', s.state.active_player)
        self.assertFalse(s.state.cards[first.object_id].tapped)
        self.finish_turn(s)
        self.assertEqual('C', s.state.active_player)
        self.assertFalse(s.state.cards[second.object_id].tapped)
        self.private(s)
        self.replay(s, load=True)
