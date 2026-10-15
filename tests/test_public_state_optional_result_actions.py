from __future__ import annotations

import unittest


from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_original_optional_have_modifier_targets_before_resolution_and_replays(self):
        session = self.session(3640401)
        engine = session.engine
        observer = self.add(engine, 'Blightcaster', zone='hand', ref='optional-have-blightcaster')
        target = self.add(engine, 'Generic Bound Body', seat='B', ref='optional-have-target')
        spell = self.add(engine, 'Thran Vigil', zone='hand', ref='optional-have-enchantment')
        second = self.add(engine, 'Thran Vigil', zone='hand', ref='optional-have-decline-spell')
        for name in (observer.printed_name, spell.printed_name):
            self.assert_whole_admission(name)
        action = self.ready(session, observer, {'B': 8, 'C': 3})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        for index, card in enumerate((spell, second)):
            while session.state.priority_player != 'A':
                self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
            action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                          if a.get('card') == card.ref)
            self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
            self.assertEqual('semantic.target', session.state.pending_decision.kind)
            before = session.state.to_dict()
            self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'targets': [target.ref]}).ok)
            self.assertEqual(before, session.state.to_dict())
            self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]}).ok)
            decision = self.resolve(session)
            self.assertEqual('semantic.choice', decision.kind)
            self.replay(session, load=True)
            before = session.state.to_dict()
            self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'choice': 'apply'}).ok)
            self.assertEqual(before, session.state.to_dict())
            power = int(engine._effective_card_data(target)['power'])
            self.assertTrue(session.act('pilot:A', {'action_id': 'choose',
                'choice': 'apply' if index == 0 else 'decline'}).ok)
            self.resolve(session)
            self.assertEqual(power - (2 if index == 0 else 0), int(engine._effective_card_data(target)['power']))
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)

    def test_original_optional_have_target_blink_prevents_new_incarnation_substitution(self):
        session = self.session(3640403)
        engine = session.engine
        observer = self.add(engine, 'Blightcaster', ref='optional-have-blink-observer')
        target = self.add(engine, 'Generic Bound Body', ref='optional-have-blink-target')
        spell = self.add(engine, 'Thran Vigil', zone='hand', ref='optional-have-blink-enchantment')
        blink = self.add(engine, 'Cloudshift', zone='hand', ref='optional-have-native-cloudshift')
        self.assert_whole_admission(observer.printed_name)
        action = self.ready(session, spell, {'B': 4, 'C': 1, 'W': 4})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]}).ok)
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == blink.ref)
        old_incarnation = target.logical_object_id
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}).ok)
        self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertNotEqual(old_incarnation, current.logical_object_id)
        self.assertEqual(self.db.lookup('Generic Bound Body').power, engine._effective_card_data(current)['power'])
        self.assertEqual('battlefield', current.zone)
        self.replay(session, load=True)

    def test_original_optional_have_source_damage_preserves_recipient_set_and_replays(self):
        session = self.session(3640402)
        engine = session.engine
        observer = self.add(engine, 'Forerunner of the Empire', ref='optional-have-forerunner')
        own = self.add(engine, 'Generic Bound Body', ref='optional-have-own-body')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', ref='optional-have-foreign-body')
        dinosaur = self.add(engine, 'Sky Terror', zone='hand', ref='optional-have-native-dinosaur')
        self.assert_whole_admission(observer.printed_name)
        self.assert_whole_admission(dinosaur.printed_name)
        action = self.ready(session, dinosaur, {'R': 1, 'W': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        self.assertEqual('battlefield', session.state.cards[dinosaur.object_id].zone)
        self.assertEqual(0, observer.marked_damage)
        self.replay(session, load=True)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'choice': 'apply'}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'choice': 'apply'}).ok)
        self.resolve(session)
        for card in (observer, own, foreign, dinosaur):
            self.assertEqual(1, session.state.cards[card.object_id].marked_damage)
        self.assertEqual('battlefield', session.state.cards[dinosaur.object_id].zone)
        for seat in 'ABCD':
            self.assertEqual(40, session.state.players[seat].life)
        self.replay(session, load=True)
