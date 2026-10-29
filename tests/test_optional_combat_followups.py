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

    def test_actual_silkenfist_sources_untap_on_becoming_blocked_without_leaving_combat(self):
        for name in ('Silkenfist Fighter', 'Silkenfist Order'):
            with self.subTest(name=name):
                s = self.session(613655250 + len(name))
                source = self.add(s.engine, name, zone='hand')
                blocker = self.add(s.engine, 'Grizzly Bears', seat='B')
                self.add(s.engine, 'Fervor')
                self.assert_closed(s, source)
                self.ready(s, {'C': 3, 'W': 2})
                self.cast(s, 'A', source)
                self.settle(s)
                self.attack(s, [(source, 'B')])
                self.assertTrue(s.state.cards[source.object_id].tapped)
                self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.blockers')
                r = s.act('pilot:B', {'action_id': 'block', 'blocks': {blocker.ref: source.ref}})
                self.assertTrue(r.ok, r.summary)
                self.assertEqual(1, self.trigger_count(s, source))
                s = self.replay(s, load=True)
                self.resolve_top(s)
                self.assertFalse(s.state.cards[source.object_id].tapped)
                self.assertEqual('B', s.state.cards[source.object_id].attacking)
                life = s.state.players['B'].life
                self.until(s, lambda s: s.state.phase_index >= 9)
                self.assertEqual(life, s.state.players['B'].life)
                self.assertEqual(2, s.state.cards[source.object_id].marked_damage)
                self.replay(s, load=True)

    def test_actual_cold_eyed_and_warped_starwinder_draw_all_actual_combat_damage_or_none(self):
        for name in ('Cold-Eyed Selkie', 'Starwinder'):
            for apply in (False, True):
                with self.subTest(name=name, apply=apply):
                    s = self.session(613655300 + len(name) + apply)
                    source = self.add(s.engine, name, zone='hand')
                    attacker = source if name == 'Cold-Eyed Selkie' else self.add(s.engine, 'Grizzly Bears')
                    buff = self.add(s.engine, 'Giant Growth', zone='hand')
                    self.add(s.engine, 'Fervor')
                    self.assert_closed(s, source)
                    self.ready(s, {'C': 2, 'U': 2, 'G': 1} if name == 'Starwinder' else {'C': 1, 'G': 3})
                    if name == 'Starwinder':
                        self.priority(s, 'A')
                        r = s.act('pilot:A', {'action_id': self.action(s, 'A', source)['id'], 'cost_option': 'warp', 'pay': 'manual', 'payment': {'C': 2, 'U': 2}})
                        self.assertTrue(r.ok, r.summary)
                    else:
                        self.cast_for(s, 'A', source, reserve={'G': 1})
                    self.settle(s)
                    self.cast(s, 'A', buff, [attacker.ref])
                    self.settle(s)
                    amount = 4 if name == 'Cold-Eyed Selkie' else 5
                    self.assertEqual(amount, s.engine._numeric_stat(attacker.object_id, 'power'))
                    hand = len(s.state.players['A'].zones['hand'])
                    hidden = [s.state.cards[oid].ref for oid in s.state.players['A'].zones['library'][-amount:]]
                    life = s.state.players['B'].life
                    self.attack(s, [(attacker, 'B')])
                    self.until(s, lambda s: self.trigger_count(s, source) == 1)
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    self.assertEqual(life - amount, s.state.players['B'].life)
                    s = self.optional(s, apply)
                    self.settle(s)
                    self.assertEqual(hand + (amount if apply else 0), len(s.state.players['A'].zones['hand']))
                    for seat in 'BCD':
                        for ref in hidden:
                            self.assertNotIn(ref, json.dumps(s.packet('pilot:' + seat, full=True)))
                    if name == 'Starwinder':
                        self.step(s, 'A', 'ending', 'end_step')
                        self.settle(s)
                        self.assertEqual('exile', s.state.cards[source.object_id].zone)
                        self.assertIn('temporary_play_permission', s.state.cards[source.object_id].annotations)
                    self.replay(s, load=True)

    def test_actual_kinscaer_announces_target_before_optional_flying_removal_and_accepts_nonflying_target(self):
        for scene in ('apply', 'decline', 'nonflying'):
            with self.subTest(scene=scene):
                s = self.session(613655350 + len(scene))
                source = self.add(s.engine, 'Kinscaer Harpoonist', zone='hand')
                target = self.add(s.engine, 'Grizzly Bears' if scene == 'nonflying' else 'Concordia Pegasus', seat='B')
                self.add(s.engine, 'Fervor')
                self.assert_closed(s, source)
                self.ready(s, {'C': 3, 'U': 1})
                self.cast(s, 'A', source)
                self.settle(s)
                self.attack(s, [(source, 'B')])
                self.target(s, 'A', [target.ref])
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, scene != 'decline')
                self.settle(s)
                self.assertEqual(scene == 'decline', 'flying' in s.engine._combat_keywords(s.state.cards[target.object_id]))
                self.finish_turn(s)
                self.assertEqual(scene != 'nonflying', 'flying' in s.engine._combat_keywords(s.state.cards[target.object_id]))
                self.replay(s, load=True)
