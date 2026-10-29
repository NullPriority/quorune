"""Actual attached combat damage carries private returns, draws and searches."""
import json
import unittest
import attachment_damage_fixture as fixtures
from quorune.record import authoritative_state_hash


class AttachmentDamageChoiceNativeTests(fixtures.AttachmentDamageFixture, unittest.TestCase):
    def test_actual_necromantic_thirst_returns_only_its_controller_graveyard_target_after_source_destruction(self):
        s = self.session(120659200)
        aura = self.add(s.engine, 'Necromantic Thirst', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears')
        body.acquired_control_turn_count = -1
        target = self.add(s.engine, 'Shivan Dragon', zone='graveyard')
        foreign = self.add(s.engine, 'Shivan Dragon', seat='B', zone='graveyard', ref='foreign-return-target')
        removal = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B')
        self.add(s.engine, 'Plains', seat='B')
        self.assert_closed(s, aura)
        self.ready(s, {'C': 2, 'B': 2})
        self.attach_cast(s, aura, body)
        self.attack(s, [(body, 'B')])
        self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'semantic.target')
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'targets': [foreign.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.target(s, 'A', [target.ref])
        self.cast(s, 'B', removal, [aura.ref])
        s = self.replay(s, load=True)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_as(s, 'A', choice='apply')
        self.settle(s)
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        self.assertEqual('hand', s.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[foreign.object_id].zone)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_one_with_nature_searches_privately_tapped_then_shuffles_or_declines(self):
        for accept in (False, True):
            with self.subTest(accept=accept):
                s = self.session(120659210 + accept)
                aura = self.add(s.engine, 'One with Nature', zone='hand')
                body = self.add(s.engine, 'Grizzly Bears')
                body.acquired_control_turn_count = -1
                land = self.add(s.engine, 'Island', zone='library', ref='private-search-island')
                self.assert_closed(s, aura)
                self.ready(s, {'G': 1})
                self.attach_cast(s, aura, body)
                self.attack(s, [(body, 'C')])
                self.damage_pending(s, aura)
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, accept)
                shuffles = s.state.players['A'].stats.get('shuffle_count', 0)
                if accept:
                    self.assertEqual('semantic.search', self.resolve(s).kind)
                    for seat in 'BCD':
                        packet = s.packet('pilot:' + seat, full=True)
                        self.assertIsNone(packet['decision'])
                        self.assertNotIn(land.ref, json.dumps(packet))
                    s = self.replay(s, load=True)
                    self.choose_as(s, 'A', search_cards=[land.ref])
                self.settle(s)
                self.assertEqual('battlefield' if accept else 'library', s.state.cards[land.object_id].zone)
                if accept:
                    self.assertTrue(s.state.cards[land.object_id].tapped)
                self.assertEqual(shuffles + accept, s.state.players['A'].stats.get('shuffle_count', 0))
                self.private(s)
                self.replay(s, load=True)

    def test_actual_sleepers_robe_opponent_relation_uses_aura_controller_and_optional_draw(self):
        for index, (recipient, accept) in enumerate((('A', False), ('C', False), ('C', True))):
            with self.subTest(recipient=recipient, accept=accept):
                s = self.session(120659220 + index)
                aura = self.add(s.engine, "Sleeper's Robe", zone='hand')
                body = self.add(s.engine, 'Grizzly Bears', seat='B')
                body.acquired_control_turn_count = -1
                self.assert_closed(s, aura)
                self.ready(s, {'U': 1, 'B': 1})
                self.attach_cast(s, aura, body)
                self.step(s, 'B', 'combat', 'declare_attackers')
                hand = {seat: len(s.state.players[seat].zones['hand']) for seat in 'AB'}
                life = s.state.players[recipient].life
                self.attack_for(s, 'B', [(body, recipient)])
                self.until(s, lambda s: s.state.players[recipient].life < life)
                self.assertEqual(0 if recipient == 'A' else 1, self.trigger_count(s, aura))
                if recipient != 'A':
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    s = self.optional(s, accept)
                self.settle(s)
                self.assertEqual(hand['A'] + accept, len(s.state.players['A'].zones['hand']))
                self.assertEqual(hand['B'], len(s.state.players['B'].zones['hand']))
                self.private(s)
                self.replay(s, load=True)
