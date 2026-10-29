"""Original-card optional result composition witnesses."""
import json
from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import optional_result_followup_fixture as followups

class OptionalResultFollowupTests(followups.OptionalResultFollowupFixture, unittest.TestCase):

    def test_actual_archon_and_orchard_optional_entry_values_use_buffed_departed_entrant_lki(self):
        for name, entrant_name, characteristic, printed in (('Archon of Redemption', 'Concordia Pegasus', 'power', 1), ('Orchard Warden', 'Ironroot Treefolk', 'toughness', 5)):
            with self.subTest(name=name):
                s = self.session(613655240 + printed)
                source = self.add(s.engine, name, zone='hand')
                entrant = self.add(s.engine, entrant_name, zone='hand')
                ordinary = self.add(s.engine, 'Grizzly Bears', zone='hand')
                buff = self.add(s.engine, 'Giant Growth', zone='hand')
                bounce = self.add(s.engine, 'Unsummon', zone='hand')
                self.assert_closed(s, source)
                self.ready(s, {'C': 12, 'W': 3, 'G': 5, 'U': 1})
                self.cast(s, 'A', source)
                if name == 'Archon of Redemption':
                    before = s.state.players['A'].life
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    s = self.optional(s, True)
                    self.settle(s)
                    self.assertEqual(before + 3, s.state.players['A'].life)
                else:
                    self.settle(s)
                    self.assertEqual(0, self.trigger_count(s, source))
                self.cast(s, 'A', ordinary)
                self.settle(s)
                self.assertEqual(0, self.trigger_count(s, source))
                life = s.state.players['A'].life
                self.cast(s, 'A', entrant)
                self.resolve_top(s)
                self.assertEqual(1, self.trigger_count(s, source))
                self.cast(s, 'A', buff, [entrant.ref])
                self.resolve_top(s)
                self.assertEqual(printed + 3, s.engine._numeric_stat(entrant.object_id, characteristic))
                self.cast(s, 'A', bounce, [entrant.ref])
                self.resolve_top(s)
                self.assertEqual('hand', s.state.cards[entrant.object_id].zone)
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, True)
                self.settle(s)
                self.assertEqual(life + printed + 3, s.state.players['A'].life)
                self.replay(s, load=True)

    def test_actual_mirror_sigil_checks_blue_twice_and_copies_departed_source_with_retriggering_ability(self):
        for scene in ('absent', 'blue_removed', 'source_removed'):
            with self.subTest(scene=scene):
                s = self.session(613655270 + len(scene))
                source = self.add(s.engine, 'Mirror-Sigil Sergeant', zone='hand')
                blue = self.add(s.engine, 'Merfolk Secretkeeper') if scene != 'absent' else None
                bounce = self.add(s.engine, 'Unsummon', zone='hand')
                self.add(s.engine, 'Island')
                self.assert_closed(s, source)
                self.ready(s, {'C': 5, 'W': 1})
                self.cast(s, 'A', source)
                self.settle(s)
                self.step(s, 'B', 'beginning', 'upkeep')
                self.assertEqual(0, self.trigger_count(s, source))
                self.step(s, 'A', 'beginning', 'upkeep')
                self.assertEqual(0 if scene == 'absent' else 1, self.trigger_count(s, source))
                if scene == 'absent':
                    self.assertFalse(any((c.is_token for c in s.state.cards.values())))
                    continue
                target = blue if scene == 'blue_removed' else source
                self.cast(s, 'A', bounce, [target.ref])
                self.resolve_top(s)
                if scene == 'blue_removed':
                    self.settle(s)
                    self.assertFalse(any((c.is_token for c in s.state.cards.values())))
                    self.replay(s, load=True)
                    continue
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, True)
                self.settle(s)
                tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
                self.assertEqual(1, len(tokens))
                copied = tokens[0]
                self.assertEqual((4, 4), tuple((s.engine._numeric_stat(copied.object_id, k) for k in ('power', 'toughness'))))
                self.step(s, 'A', 'beginning', 'upkeep')
                self.assertEqual(1, self.trigger_count(s, copied))
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, True)
                self.settle(s)
                self.assertEqual(2, sum((c.is_token and c.zone == 'battlefield' for c in s.state.cards.values())))
                self.replay(s, load=True)

    def test_actual_clearwater_sunburst_counts_four_colors_and_optional_upkeep_gains_all_or_zero(self):
        s = self.session(613655370)
        source = self.add(s.engine, 'Clearwater Goblet', zone='hand')
        self.assert_closed(s, source)
        self.ready(s, {'C': 1, 'W': 1, 'U': 1, 'R': 1, 'G': 1})
        self.priority(s, 'A')
        r = s.act('pilot:A', {'action_id': self.action(s, 'A', source)['id'], 'pay': 'manual', 'payment': {'C': 1, 'W': 1, 'U': 1, 'R': 1, 'G': 1}})
        self.assertTrue(r.ok, r.summary)
        self.settle(s)
        self.assertEqual(4, s.state.cards[source.object_id].counters.get('charge', 0))
        life = s.state.players['A'].life
        self.step(s, 'B', 'beginning', 'upkeep')
        self.assertEqual(0, self.trigger_count(s, source))
        self.step(s, 'A', 'beginning', 'upkeep')
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, True)
        self.settle(s)
        self.assertEqual(life + 4, s.state.players['A'].life)
        self.step(s, 'A', 'beginning', 'upkeep')
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, False)
        self.settle(s)
        self.assertEqual(life + 4, s.state.players['A'].life)
        self.replay(s, load=True)
