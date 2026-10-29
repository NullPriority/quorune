"""Current versus retained empty-library Explore under real command replay."""
import unittest
import source_explore_fixture as fixtures


class ExploreModeNativeTests(fixtures.SourceExploreFixture, unittest.TestCase):
    def test_actual_branchwalker_empty_explore_current_and_unversioned_legacy_modes_replay(self):
        for current in (False, True):
            with self.subTest(current=current):
                s = self.session(613658201 + current)
                s.state.explore_action_version = 1 if current else None
                source = self.add(s.engine, 'Merfolk Branchwalker', zone='hand')
                self.assert_closed(s, source)
                self.empty_library(s)
                self.ready(s, {'C': 1, 'G': 1})
                self.cast(s, 'A', source)
                self.settle(s)
                self.assertEqual(int(current), s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.assertEqual((3, 2) if current else (2, 1), tuple(s.engine._numeric_stat(source.object_id, k) for k in ('power', 'toughness')))
                self.assertEqual(1, sum(e.code == 'explore.complete' for e in s.state.events))
                self.assertEqual(current, 'explore_action_version' in s.state.to_dict())
                s = self.replay(s, load=True)
                self.assertEqual(1 if current else None, s.state.explore_action_version)

    def test_actual_map_builtin_empty_explore_uses_same_versioned_counter_owner(self):
        for current in (False, True):
            with self.subTest(current=current):
                s = self.session(613658220 + current)
                s.state.explore_action_version = 1 if current else None
                source = self.add(s.engine, 'Sentinel of the Nameless City', zone='hand')
                self.assert_closed(s, source)
                self.empty_library(s)
                self.ready(s, {'C': 3, 'G': 1})
                self.cast_for(s, 'A', source, reserve={'C': 1})
                self.settle(s)
                token = next(c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield')
                ability = s.engine._activated_abilities(token)[0]
                self.assertEqual('builtin:explore-target', ability.builtin_semantic_key)
                self.activate(s, 'A', token, [source.ref])
                self.settle(s)
                self.assertEqual(int(current), s.state.cards[source.object_id].counters.get('+1/+1', 0))
                self.assertNotIn(token.object_id, s.state.players['A'].zones['battlefield'])
                self.assertEqual(1, sum(e.code == 'explore.complete' for e in s.state.events))
                self.replay(s, load=True)

    def test_actual_current_empty_explore_counter_replacements_resume_once_and_legacy_remains_empty(self):
        for current in (False, True):
            with self.subTest(current=current):
                s = self.session(613658240 + current)
                s.state.explore_action_version = 1 if current else None
                source = self.add(s.engine, 'Merfolk Branchwalker', zone='hand')
                scales = self.add(s.engine, 'Hardened Scales')
                borrowed_scales = self.add(s.engine, 'Hardened Scales', seat='B', ref='borrowed-explore-scales')
                s.engine.change_control(borrowed_scales.object_id, 'A', reason='Prepared borrowed counter-replacement source')
                for card in (source, scales, borrowed_scales):
                    self.assert_closed(s, card)
                self.empty_library(s)
                self.ready(s, {'C': 1, 'G': 1})
                self.cast(s, 'A', source)
                decision = self.resolve(s)
                if current:
                    import json
                    from quorune.record import authoritative_state_hash
                    self.assertEqual('replacement.order', decision.kind)
                    self.assertEqual(0, sum(e.code == 'explore.complete' for e in s.state.events))
                    for _ in range(4):
                        if s.state.pending_decision is None or s.state.pending_decision.kind != 'replacement.order':
                            break
                        packet = s.packet('pilot:A', full=True)['decision']
                        self.assertNotIn('semantic_intent', json.dumps(packet))
                        selected = packet['ctx']['options'][0]['id']
                        command = {'action_id': 'choose', 'choices': {'replacement': selected}}
                        before = authoritative_state_hash(s.state)
                        self.assertFalse(s.act('pilot:B', command).ok)
                        self.assertEqual(before, authoritative_state_hash(s.state))
                        s = self.replay(s, load=True)
                        r = s.act('pilot:A', command)
                        self.assertTrue(r.ok, r.summary)
                    self.settle(s)
                    self.assertEqual(3, s.state.cards[source.object_id].counters.get('+1/+1', 0))
                    self.assertEqual(1, sum(e.code == 'explore.complete' for e in s.state.events))
                    self.assertEqual(0, sum(e.code == 'library.look' for e in s.state.events))
                    self.replay(s, load=True)
                else:
                    self.assertIsNone(decision)
                    self.assertEqual({}, s.state.cards[source.object_id].counters)
                    self.assertEqual(1, sum(e.code == 'explore.complete' for e in s.state.events))
                    self.replay(s, load=True)
