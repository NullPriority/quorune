"""Actual source-bound combat Explore and current/LKI controller behavior."""
import json
import unittest
import source_explore_fixture as fixtures


class SourceExploreNativeTests(fixtures.SourceExploreFixture, unittest.TestCase):
    def test_actual_vanguard_combat_explores_land_nonland_and_empty_library(self):
        for scene in ('land', 'nonland', 'empty'):
            with self.subTest(scene=scene):
                s = self.session(613658100 + len(scene))
                source = self.add(s.engine, "Emperor's Vanguard", zone='hand')
                self.add(s.engine, 'Fervor')
                self.empty_library(s)
                hidden = self.add(s.engine, 'Divination', zone='library', ref='explore-hidden-library') if scene != 'empty' else None
                top = self.add(s.engine, 'Forest' if scene == 'land' else 'Sol Ring', zone='library', ref='explore-revealed-top') if scene != 'empty' else None
                self.assert_closed(s, source)
                self.ready(s, {'C': 3, 'G': 1})
                self.cast(s, 'A', source)
                self.settle(s)
                hand = len(s.state.players['A'].zones['hand'])
                life = s.state.players['B'].life
                self.attack(s, [(source, 'B')])
                self.until(s, lambda s: self.trigger_count(s, source) == 1)
                self.assertEqual(life - 4, s.state.players['B'].life)
                decision = self.resolve(s)
                if scene == 'nonland':
                    self.assertEqual('semantic.choice', decision.kind)
                    self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                    for seat in 'BCD':
                        packet = json.dumps(s.packet('pilot:' + seat, full=True))
                        self.assertIn(top.ref, packet)
                        self.assertNotIn(hidden.ref, packet)
                    s = self.explore_choice(s, 'A', 'graveyard')
                else:
                    self.assertIsNone(decision)
                self.settle(s)
                self.assertEqual(0 if scene == 'land' else 1, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.assertEqual(hand + (scene == 'land'), len(s.state.players['A'].zones['hand']))
                if top is not None:
                    self.assertEqual('hand' if scene == 'land' else 'graveyard', s.state.cards[top.object_id].zone)
                self.assertEqual(1, sum(e.code == 'explore.complete' for e in s.state.events))
                self.private(s)
                self.replay(s, load=True)

    def test_actual_jenny_double_strike_creates_two_separate_explores_and_first_counter_changes_later_damage(self):
        s = self.session(613658120)
        source = self.add(s.engine, 'Jenny, Generated Anomaly', zone='hand')
        self.add(s.engine, 'Fervor')
        top = self.add(s.engine, 'Sol Ring', zone='library', ref='jenny-revealed-top')
        self.assert_closed(s, source)
        self.ready(s, {'C': 2, 'R': 1, 'W': 1})
        self.cast(s, 'A', source)
        self.settle(s)
        life = s.state.players['B'].life
        self.attack(s, [(source, 'B')])
        self.until(s, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual(life - 2, s.state.players['B'].life)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1'))
        s = self.explore_choice(s, 'A', 'top')
        self.settle(s)
        self.assertEqual(1, sum(e.code == 'explore.complete' for e in s.state.events))
        self.until(s, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual(life - 5, s.state.players['B'].life)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('+1/+1'))
        s = self.explore_choice(s, 'A', 'graveyard')
        self.settle(s)
        self.assertEqual('graveyard', s.state.cards[top.object_id].zone)
        self.assertEqual((4, 5), tuple(s.engine._numeric_stat(source.object_id, k) for k in ('power', 'toughness')))
        self.assertEqual(2, sum(e.code == 'explore.complete' for e in s.state.events))
        self.replay(s, load=True)

    def test_actual_vanguard_uses_current_or_departure_controller_and_never_counters_returned_incarnation(self):
        for scene in ('current', 'departed', 'returned'):
            with self.subTest(scene=scene):
                s = self.session(613658140 + len(scene))
                source = self.add(s.engine, "Emperor's Vanguard", zone='hand')
                theft = self.add(s.engine, 'Act of Treason', seat='B', zone='hand')
                bounce = self.add(s.engine, 'Unsummon', zone='hand')
                self.add(s.engine, 'Fervor')
                self.add(s.engine, 'Vedalken Orrery', ref='explore-A-orrery')
                self.add(s.engine, 'Vedalken Orrery', seat='B', ref='explore-B-orrery')
                for i in range(3):
                    self.add(s.engine, 'Mountain', seat='B', ref='explore-B-mana-' + str(i))
                for i in range(4):
                    self.add(s.engine, 'Forest', ref='explore-recast-mana-' + str(i))
                self.add(s.engine, 'Island')
                top = self.add(s.engine, 'Sol Ring', seat='B', zone='library', ref='explore-B-revealed-top')
                private = self.add(s.engine, 'Divination', seat='B', zone='hand', ref='explore-B-private-hand')
                self.assert_closed(s, source)
                self.assert_closed(s, theft)
                self.ready(s, {'C': 3, 'G': 1})
                self.cast(s, 'A', source)
                self.settle(s)
                self.attack(s, [(source, 'B')])
                self.until(s, lambda s: self.trigger_count(s, source) == 1)
                old_identity = s.state.cards[source.object_id].logical_object_id
                self.cast(s, 'B', theft, [source.ref])
                self.resolve_top(s)
                self.assertEqual('B', s.state.cards[source.object_id].controller)
                if scene != 'current':
                    self.cast(s, 'A', bounce, [source.ref])
                    self.resolve_top(s)
                    self.assertEqual('hand', s.state.cards[source.object_id].zone)
                    if scene == 'returned':
                        self.cast(s, 'A', source)
                        self.resolve_top(s)
                        self.assertNotEqual(old_identity, s.state.cards[source.object_id].logical_object_id)
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.assertEqual(['B'], s.state.pending_decision.actors)
                self.assertEqual(1 if scene == 'current' else 0, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                for seat in 'ACD':
                    self.assertNotIn(private.ref, json.dumps(s.packet('pilot:' + seat, full=True)))
                s = self.explore_choice(s, 'B', 'graveyard')
                self.settle(s)
                self.assertEqual('graveyard', s.state.cards[top.object_id].zone)
                self.assertEqual(1, sum(e.code == 'explore.complete' for e in s.state.events))
                self.replay(s, load=True)
