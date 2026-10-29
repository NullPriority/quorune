"""Actual Casualty casts sacrifice before one independently retargetable copy."""
import unittest
import casualty_fixture as fixtures
from quorune.record import authoritative_state_hash


class CasualtyNativeTests(fixtures.CasualtyFixture, unittest.TestCase):
    def test_actual_casualty_current_power_buff_and_original_counter_do_not_erase_retained_copy(self):
        s = self.session(702153003)
        source = self.add(s.engine, "Light 'Em Up", zone='hand')
        price = self.add(s.engine, 'Llanowar Elves')
        grow = self.add(s.engine, 'Giant Growth', zone='hand')
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        targets = [self.add(s.engine, 'Grizzly Bears', seat=p, ref='countered-casualty-target-' + p) for p in 'BC']
        s.state.players['B'].mana_pool['U'] = 2
        self.assert_closed(s, source);self.ready(s, {'C': 1, 'R': 1, 'G': 1})
        offered = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)
        self.assertFalse(any('+casualty:' in row['id'] for row in offered['cost_options']))
        self.cast_for(s, 'A', grow, [price.ref], reserve={'C': 1, 'R': 1});self.settle(s)
        self.assertEqual(4, s.engine._numeric_stat(s.state.cards[price.object_id].object_id, 'power'))
        self.cast_casualty(s, source, price, [targets[0].ref])
        original = next(item for item in s.state.stack if item.kind == 'spell')
        self.cast(s, 'B', counter, [original.ref])
        self.assertEqual('semantic.storm', self.resolve(s).kind)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        s = self.replay(s, load=True)
        self.choose_copy(s, [targets[1].ref]);self.settle(s)
        self.assertEqual('battlefield', s.state.cards[targets[0].object_id].zone)
        self.assertEqual('graveyard', s.state.cards[targets[1].object_id].zone)
        self.assertEqual(3, len(s.engine._current_turn_history('spell_cast')))
        self.private(s);self.replay(s, load=True)

    def test_actual_casualty_redirected_sacrifice_retains_private_pending_cast_and_exact_replay(self):
        s = self.session(702153004)
        source = self.add(s.engine, 'Join the Maestros', zone='hand')
        price = self.add(s.engine, 'Grizzly Bears')
        for i in range(2):self.add(s.engine, 'Dauthi Voidwalker', seat='B', ref='casualty-destination-' + str(i))
        self.assert_closed(s, source);self.ready(s, {'C': 4, 'B': 1})
        self.cast_casualty(s, source, price)
        self.assertEqual('replacement.order', s.state.pending_decision.kind)
        self.assertEqual('battlefield', s.state.cards[price.object_id].zone)
        self.assertNotIn('private-recipient-hand', str(s.packet('pilot:C', full=True)))
        s = self.replay(s, load=True)
        while s.state.pending_decision.kind == 'replacement.order':
            principal = s.pending_principals()[0]
            option = s.packet(principal, full=True)['decision']['ctx']['options'][0]['id']
            before = authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:D', {'action_id': 'choose', 'replacement': option}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
            accepted = s.act(principal, {'action_id': 'choose', 'replacement': option})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('exile', s.state.cards[price.object_id].zone)
        for _ in range(8):
            self.resolve(s)
            if s.state.pending_decision.kind != 'replacement.order':break
            principal = s.pending_principals()[0]
            option = s.packet(principal, full=True)['decision']['ctx']['options'][0]['id']
            accepted = s.act(principal, {'action_id': 'choose', 'replacement': option})
            self.assertTrue(accepted.ok, accepted.summary)
        self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.is_token and c.controller == 'A']
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(s.engine._numeric_stat(c.object_id, 'power') == 4 for c in tokens))
        self.assertEqual(1, len(s.engine._current_turn_history('spell_cast')))
        self.private(s);self.replay(s, load=True)

    def test_actual_make_disappear_copy_and_original_issue_independent_target_controller_payments(self):
        s = self.session(702153005)
        source = self.add(s.engine, 'Make Disappear', zone='hand')
        price = self.add(s.engine, 'Grizzly Bears')
        victim = self.add(s.engine, 'Divination', seat='B', zone='hand', ref='casualty-counter-victim')
        self.add(s.engine, 'Vedalken Orrery', seat='B')
        s.state.players['B'].mana_pool.update({'C': 4, 'U': 1})
        self.assert_closed(s, source);self.ready(s, {'C': 1, 'U': 1})
        self.cast(s, 'B', victim)
        target = next(item.ref for item in s.state.stack if item.card_object_id == victim.object_id)
        self.cast_casualty(s, source, price, [target])
        self.assertEqual('semantic.storm', self.resolve(s).kind)
        self.choose_copy(s, [target])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(['pilot:B'], s.pending_principals())
        s = self.replay(s, load=True)
        first = s.act('pilot:B', {'action_id': 'choose', 'pay': True});self.assertTrue(first.ok, first.summary)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        second = s.act('pilot:B', {'action_id': 'choose', 'pay': False});self.assertTrue(second.ok, second.summary)
        self.settle(s)
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.assertEqual(0, s.state.players['B'].mana_pool['C'])
        self.assertEqual(2, len(s.engine._current_turn_history('spell_cast')))
        self.private(s);self.replay(s, load=True)

    def test_actual_casualty_power_offer_reads_hand_size_after_cast_source_placement(self):
        s = self.session(702153006)
        for oid in tuple(s.state.players['A'].zones['hand']):s.engine.move_card(oid, 'library', log=False)
        source = self.add(s.engine, "Light 'Em Up", zone='hand')
        price = self.add(s.engine, 'Maro')
        target = self.add(s.engine, 'Grizzly Bears', seat='B')
        self.assert_closed(s, source);self.assert_closed(s, price)
        self.ready(s, {'C': 1, 'R': 1})
        self.assertEqual(2, len(s.state.players['A'].zones['hand']))
        self.assertEqual(2, s.engine._numeric_stat(price.object_id, 'power'))
        offered = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)
        self.assertFalse(any('+casualty:' in row['id'] for row in offered['cost_options']))
        self.cast(s, 'A', source, [target.ref]);self.settle(s)
        self.assertEqual('battlefield', s.state.cards[price.object_id].zone)
        self.assertEqual(1, s.engine._numeric_stat(price.object_id, 'power'))
        self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
        self.private(s);self.replay(s, load=True)

    def test_actual_light_em_up_sacrifices_a_tapped_current_creature_and_retargets_once(self):
        s = self.session(702153001)
        source = self.add(s.engine, "Light 'Em Up", zone='hand')
        price = self.add(s.engine, 'Grizzly Bears', ref='casualty-tapped-price');price.tapped = True
        small = self.add(s.engine, 'Ornithopter')
        foreign = self.add(s.engine, 'Gigantosaurus', seat='B', ref='foreign-casualty-price')
        targets = [self.add(s.engine, 'Grizzly Bears', seat=p, ref='casualty-target-' + p) for p in 'BC']
        self.assert_closed(s, source);self.ready(s, {'C': 1, 'R': 1})
        action, option = self.casualty_action(s, source)
        self.assertIn(price.ref, option['choice_schema']['sacrifice_cards']['legal_refs'])
        self.assertNotIn(small.ref, option['choice_schema']['sacrifice_cards']['legal_refs'])
        self.assertNotIn(foreign.ref, option['choice_schema']['sacrifice_cards']['legal_refs'])
        before = authoritative_state_hash(s.state)
        for refs in ([small.ref], [foreign.ref], [price.ref, small.ref]):
            self.assertFalse(s.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'], 'sacrifice_cards': refs, 'targets': [targets[0].ref], 'pay': 'auto'}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        self.cast_casualty(s, source, price, [targets[0].ref])
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        self.assertEqual(2, len(s.state.stack))
        self.assertEqual('semantic.storm', self.resolve(s).kind)
        s = self.replay(s, load=True)
        self.choose_copy(s, [targets[1].ref]);self.settle(s)
        self.assertTrue(all(s.state.cards[t.object_id].zone == 'graveyard' for t in targets))
        self.assertEqual(1, len(s.engine._current_turn_history('spell_cast')))
        self.private(s);self.replay(s, load=True)

    def test_actual_join_maestros_without_eligible_price_keeps_ordinary_cast_and_one_token(self):
        s = self.session(702153002)
        source = self.add(s.engine, 'Join the Maestros', zone='hand')
        self.assert_closed(s, source);self.ready(s, {'C': 4, 'B': 1})
        action = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'] == 'cast:' + source.ref)
        self.assertFalse(any('+casualty:' in row['id'] for row in action['cost_options']))
        self.cast(s, 'A', source);self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.is_token and c.controller == 'A']
        self.assertEqual(1, len(tokens));self.assertEqual(4, s.engine._numeric_stat(tokens[0].object_id, 'power'))
        self.assertEqual(1, len(s.engine._current_turn_history('spell_cast')))
        self.private(s);self.replay(s, load=True)
