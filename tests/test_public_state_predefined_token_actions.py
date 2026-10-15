from __future__ import annotations

import unittest

import json
from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_original_lander_native_cast_search_tapped_entry_and_private_replay(self):
        session = self.session(3640601)
        engine = session.engine
        source = self.add(engine, 'Galactic Wayfarer', zone='hand', ref='predefined-lander-native-wayfarer')
        nonbasic = self.add(engine, 'Dryad Arbor', zone='library', ref='predefined-lander-nonbasic-land')
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {'G': 3, 'C': 6})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        token = next(card for card in session.state.cards.values() if card.is_token and card.printed_name == 'Lander')
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'].startswith('activate:'+token.ref+':'))
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual('outside', session.state.cards[token.object_id].zone)
        self.assertEqual('semantic.search', self.resolve(session).kind)
        chosen = next(session.state.cards[oid] for oid in session.state.players['A'].zones['library']
                      if session.state.cards[oid].printed_name == 'Generic Bound Plains')
        self.replay(session, load=True)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'search_cards': [chosen.ref]}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.assertFalse(session.act('pilot:A', {'action_id': 'choose', 'search_cards': [nonbasic.ref]}).ok)
        self.assertEqual(before, session.state.to_dict())
        for seat in 'BCD':
            self.assertNotIn(chosen.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'search_cards': [chosen.ref]}).ok)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[chosen.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertTrue(current.tapped)
        self.assertEqual('A', current.controller)
        self.replay(session, load=True)

    def test_original_lander_native_search_can_fail_to_find_and_still_shuffle(self):
        session = self.session(3640603)
        engine = session.engine
        source = self.add(engine, 'Galactic Wayfarer', zone='hand', ref='predefined-lander-fail-find-wayfarer')
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {'G': 3, 'C': 6})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        token = next(card for card in session.state.cards.values() if card.is_token and card.printed_name == 'Lander')
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'].startswith('activate:'+token.ref+':'))
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual('semantic.search', self.resolve(session).kind)
        library_size = len(session.state.players['A'].zones['library'])
        before_shuffle = session.state.players['A'].stats.get('shuffle_count', 0)
        self.replay(session, load=True)
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'search_cards': []}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(library_size, len(session.state.players['A'].zones['library']))
        self.assertEqual(before_shuffle+1, session.state.players['A'].stats['shuffle_count'])
        self.assertFalse(any(self.db.by_oracle_id(card.oracle_id).type_line.startswith('Basic Land')
            for card in session.state.cards.values() if card.owner == 'A' and card.zone == 'battlefield'
            and card.oracle_id is not None))
        self.replay(session, load=True)

    def test_original_mutagen_native_target_blink_does_not_substitute_new_incarnation(self):
        session = self.session(3640604)
        engine = session.engine
        source = self.add(engine, 'Crustacean Commando', zone='hand', ref='predefined-mutagen-blink-commando')
        target = self.add(engine, 'Generic Bound Body', seat='B', ref='predefined-mutagen-blink-recipient')
        blink = self.add(engine, 'Cloudshift', seat='B', zone='hand', ref='predefined-mutagen-original-blink')
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {'U': 4, 'C': 4})
        session.state.players['B'].mana_pool.update({'W': 4})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        token = next(card for card in session.state.cards.values() if card.is_token and card.printed_name == 'Mutagen')
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'].startswith('activate:'+token.ref+':'))
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}).ok)
        while session.state.priority_player != 'B':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == blink.ref)
        previous_identity = target.logical_object_id
        self.assertTrue(session.act('pilot:B', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}).ok)
        self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(previous_identity, session.state.cards[target.object_id].logical_object_id)
        self.assertEqual(0, session.state.cards[target.object_id].counters.get('+1/+1', 0))
        self.replay(session, load=True)

    def test_original_mutagen_native_cast_target_counter_and_sorcery_timing_replay(self):
        session = self.session(3640602)
        engine = session.engine
        source = self.add(engine, 'Crustacean Commando', zone='hand', ref='predefined-mutagen-native-commando')
        target = self.add(engine, 'Generic Bound Body', seat='B', ref='predefined-mutagen-foreign-creature')
        invalid = self.add(engine, 'Generic Bound Plains', ref='predefined-mutagen-noncreature')
        draw = self.add(engine, 'Divination', zone='hand', ref='predefined-mutagen-native-stack-spell')
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {'U': 8, 'C': 8})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        token = next(card for card in session.state.cards.values() if card.is_token and card.printed_name == 'Mutagen')
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        draw_action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                           if a.get('card') == draw.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': draw_action['id'], 'pay': 'auto'}).ok)
        token_action = 'activate:'+token.ref+':ab1'
        self.assertNotIn(token_action, [a['id'] for a in
            session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']])
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:A', {'action_id': token_action, 'targets': [target.ref], 'pay': 'auto'}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.assertIsNone(self.resolve(session))
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'].startswith('activate:'+token.ref+':'))
        before = session.state.to_dict()
        for principal, recipient in (('pilot:B', target.ref), ('pilot:A', invalid.ref)):
            self.assertFalse(session.act(principal, {'action_id': action['id'], 'targets': [recipient], 'pay': 'auto'}).ok)
            self.assertEqual(before, session.state.to_dict())
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}).ok)
        self.assertEqual('outside', session.state.cards[token.object_id].zone)
        self.assertEqual(0, target.counters.get('+1/+1', 0))
        self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[target.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, source.counters.get('+1/+1', 0))
        self.replay(session, load=True)

    def test_original_blood_token_native_cast_cost_payment_and_private_draw_replay(self):
        session = self.session(3640501)
        engine = session.engine
        source = self.add(engine, 'Blood Servitor', zone='hand', ref='predefined-blood-native-servitor')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', zone='hand', ref='predefined-blood-foreign-cost')
        self.assert_whole_admission(source.printed_name)
        action = self.ready(session, source, {'C': 6})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        for seat in 'BCD':
            self.assertEqual(40, session.state.players[seat].life)
        blood = next(card for card in session.state.cards.values() if card.is_token and card.printed_name == 'Blood')
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'].startswith('activate:'+blood.ref+':'))
        own = session.state.cards[session.state.players['A'].zones['hand'][0]]
        top = session.state.players['A'].zones['library'][-1]
        hand_before = len(session.state.players['A'].zones['hand'])
        mana_before = sum(session.state.players['A'].mana_pool.values())
        before = session.state.to_dict()
        for principal, selected in (('pilot:B', own.ref), ('pilot:A', foreign.ref), ('pilot:A', source.ref)):
            self.assertFalse(session.act(principal, {'action_id': action['id'], 'cost_cards': [selected], 'pay': 'auto'}).ok)
            self.assertEqual(before, session.state.to_dict())
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [own.ref], 'pay': 'auto'}).ok)
        self.assertEqual('graveyard', session.state.cards[own.object_id].zone)
        self.assertEqual(mana_before-1, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('outside', session.state.cards[blood.object_id].zone)
        self.assertEqual(hand_before-1, len(session.state.players['A'].zones['hand']))
        self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand_before, len(session.state.players['A'].zones['hand']))
        self.assertEqual('hand', session.state.cards[top].zone)
        for seat in 'BCD':
            packet = session.packet('pilot:'+seat, full=True)
            self.assertNotIn('hand', packet['state']['players']['A'])
            self.assertNotIn(session.state.cards[top].ref, json.dumps(packet))
        self.replay(session, load=True)

    def test_original_blood_token_empty_hand_offer_changes_after_native_draw(self):
        session = self.session(3640502)
        engine = session.engine
        self.clear_initial_hand(session)
        source = self.add(engine, 'Blood Servitor', zone='hand', ref='predefined-blood-empty-servitor')
        drawer = self.add(engine, 'Azure Mage', ref='predefined-blood-native-drawer')
        self.assert_whole_admission(source.printed_name)
        self.assert_whole_admission(drawer.printed_name)
        action = self.ready(session, source, {'C': 9, 'U': 4})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], session.state.players['A'].zones['hand'])
        blood = next(card for card in session.state.cards.values() if card.is_token and card.printed_name == 'Blood')
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        blood_action = 'activate:'+blood.ref+':ab1'
        self.assertNotIn(blood_action, [a['id'] for a in actions])
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:A', {'action_id': blood_action, 'cost_cards': [], 'pay': 'auto'}).ok)
        self.assertEqual(before, session.state.to_dict())
        draw_action = next(a for a in actions if a['id'].startswith('activate:'+drawer.ref+':'))
        self.assertTrue(session.act('pilot:A', {'action_id': draw_action['id'], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertIn(blood_action, [a['id'] for a in actions])
        own = session.state.cards[session.state.players['A'].zones['hand'][0]]
        self.assertTrue(session.act('pilot:A', {'action_id': blood_action, 'cost_cards': [own.ref], 'pay': 'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[own.object_id].zone)
        self.assertEqual(1, len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)
