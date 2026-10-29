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

    def test_actual_swarm_optional_copy_declines_then_copies_countered_x_with_new_targets(self):
        s = self.session(613655201)
        source = self.add(s.engine, 'Swarm Intelligence', zone='hand')
        draw = self.add(s.engine, 'Divination', zone='hand', ref='swarm-draw')
        blaze = self.add(s.engine, 'Blaze', zone='hand')
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        self.assert_closed(s, source)
        self.ready(s, {'C': 11, 'U': 2, 'R': 1})
        s.state.players['B'].mana_pool['U'] = 2
        self.checkpoint(s)
        self.cast_for(s, 'A', source, reserve={'C': 5, 'U': 1, 'R': 1})
        self.settle(s)
        self.cast_for(s, 'A', draw, reserve={'C': 3, 'R': 1})
        hand = len(s.state.players['A'].zones['hand'])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, False)
        self.assertFalse(any((i.kind == 'spell_copy' for i in s.state.stack)))
        self.settle(s)
        self.assertEqual(hand + 2, len(s.state.players['A'].zones['hand']))
        self.priority(s, 'A')
        r = s.act('pilot:A', {'action_id': self.action(s, 'A', blaze)['id'], 'x': 3, 'targets': ['B'], 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)
        original = next((i for i in s.state.stack if i.card_object_id == blaze.object_id))
        self.cast(s, 'B', counter, [original.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual('graveyard', s.state.cards[blaze.object_id].zone)
        s = self.optional(s, True)
        self.retarget_copy(s, ['C'])
        copied = next((i for i in s.state.stack if i.kind == 'spell_copy'))
        self.assertEqual(3, copied.x_value)
        self.assertIsNone(copied.mana_spent_counts)
        self.assertEqual(['C'], copied.targets)
        self.assertEqual(0, self.trigger_count(s, source))
        life = {seat: s.state.players[seat].life for seat in 'BC'}
        s = self.replay(s, load=True)
        self.settle(s)
        self.assertEqual(life['B'], s.state.players['B'].life)
        self.assertEqual(life['C'] - 3, s.state.players['C'].life)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_gorion_and_chancellor_copy_adventure_face_without_copy_permission_or_normal_face_trigger(self):
        for name in ('Gorion, Wise Mentor', 'Chancellor of Tales'):
            with self.subTest(name=name):
                s = self.session(613655210 + len(name))
                source = self.add(s.engine, name, zone='hand')
                adventure = self.add(s.engine, 'Merfolk Secretkeeper', zone='hand')
                normal = self.add(s.engine, 'Merfolk Secretkeeper', zone='hand', ref='normal-adventurer')
                self.assert_closed(s, source)
                self.assert_closed(s, adventure)
                self.ready(s, {'C': 3, 'G': 1, 'W': 1, 'U': 5})
                self.cast_for(s, 'A', source, reserve={'U': 3})
                self.settle(s)
                self.cast_face(s, normal, 'Merfolk Secretkeeper')
                self.assertEqual(0, self.trigger_count(s, source))
                self.settle(s)
                self.cast_face(s, adventure, 'Venture Deeper', ['B'])
                self.assertEqual(1, self.trigger_count(s, source))
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, True)
                self.retarget_copy(s, ['C'])
                copied = next((i for i in s.state.stack if i.kind == 'spell_copy'))
                self.assertEqual('Venture Deeper', copied.chosen_face)
                self.assertEqual(0, self.trigger_count(s, source))
                libraries = {seat: len(s.state.players[seat].zones['library']) for seat in 'BC'}
                s = self.replay(s, load=True)
                self.settle(s)
                for seat in 'BC':
                    self.assertEqual(libraries[seat] - 4, len(s.state.players[seat].zones['library']))
                current = s.state.cards[adventure.object_id]
                self.assertEqual('exile', current.zone)
                from quorune.adventure import ADVENTURE_PERMISSION
                self.assertEqual('A', current.annotations[ADVENTURE_PERMISSION]['player'])
                self.assertFalse(any((c.object_kind == 'spell_copy' and c.zone == 'battlefield' for c in s.state.cards.values())))
                self.cast_face(s, adventure, 'Merfolk Secretkeeper')
                self.assertEqual(0, self.trigger_count(s, source))
                self.settle(s)
                self.assertEqual('battlefield', s.state.cards[adventure.object_id].zone)
                self.replay(s, load=True)

    def test_actual_bounteous_optional_cast_value_distinguishes_arcane_spirit_and_foreign_or_ordinary_cast(self):
        s = self.session(613655230)
        source = self.add(s.engine, 'Bounteous Kirin', zone='hand')
        spirit = self.add(s.engine, 'Lantern Kami', zone='hand')
        arcane = self.add(s.engine, 'Reach Through Mists', zone='hand')
        ordinary = self.add(s.engine, 'Grizzly Bears', zone='hand')
        foreign = self.add(s.engine, 'Reach Through Mists', seat='B', zone='hand', ref='foreign-arcane')
        self.assert_closed(s, source)
        self.ready(s, {'C': 6, 'G': 3, 'U': 1, 'W': 1})
        s.state.players['B'].mana_pool['U'] = 1
        self.checkpoint(s)
        self.cast_for(s, 'A', source, reserve={'C': 1, 'G': 1, 'U': 1, 'W': 1})
        self.settle(s)
        self.cast_for(s, 'A', ordinary, reserve={'U': 1, 'W': 1})
        self.assertEqual(0, self.trigger_count(s, source))
        self.settle(s)
        self.cast(s, 'B', foreign)
        self.assertEqual(0, self.trigger_count(s, source))
        self.settle(s)
        life = s.state.players['A'].life
        self.cast_for(s, 'A', spirit, reserve={'U': 1})
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, False)
        self.settle(s)
        self.assertEqual(life, s.state.players['A'].life)
        self.cast(s, 'A', arcane)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, True)
        self.settle(s)
        self.assertEqual(life + 1, s.state.players['A'].life)
        self.replay(s, load=True)
