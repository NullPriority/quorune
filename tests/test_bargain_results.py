"""Original linked Bargain entry bodies and same-target spell results."""
import unittest
import bargain_fixture as fixtures
from quorune.record import authoritative_state_hash


class BargainResultTests(fixtures.BargainFixture, unittest.TestCase):
    def test_actual_ordinary_clone_does_not_inherit_another_permanents_bargain_declaration(self):
        s = self.session(702166130)
        source = self.add(s.engine, 'High Fae Negotiator', zone='hand')
        clone = self.add(s.engine, 'Clone', zone='hand')
        price = self.add(s.engine, 'Fervor')
        self.assert_closed(s, source);self.ready(s, {'C': 6, 'B': 2, 'U': 1})
        self.cast_bargain(s, source, price, reserve={'C': 3, 'U': 1});self.settle(s)
        self.assertEqual([43, 37, 37, 37], [s.state.players[p].life for p in 'ABCD'])
        self.cast(s, 'A', clone)
        self.assertEqual('replacement.order', self.resolve(s).kind)
        options = s.packet('pilot:A', full=True)['decision']['ctx']['options']
        option = next(row['id'] for row in options if source.ref in row['id'])
        self.choose_as(s, 'A', replacement=option);self.settle(s)
        self.assertEqual('High Fae Negotiator', s.engine._effective_card_data(s.state.cards[clone.object_id])['name'])
        self.assertEqual([43, 37, 37, 37], [s.state.players[p].life for p in 'ABCD'])
        self.assertNotIn('declared_cast_costs', s.state.cards[clone.object_id].annotations)
        self.private(s);self.replay(s, load=True)

    def test_actual_troublemaker_ouphe_exiles_opponent_object_after_enchantment_sacrifice(self):
        s = self.session(702166101)
        source = self.add(s.engine, 'Troublemaker Ouphe', zone='hand')
        price = self.add(s.engine, 'Fervor')
        own = self.add(s.engine, 'Sol Ring')
        target = self.add(s.engine, 'Ornithopter', seat='B')
        self.assert_closed(s, source);self.ready(s, {'C': 1, 'G': 1})
        self.cast_bargain(s, source, price)
        self.resolve(s)
        self.assertEqual('semantic.target', s.state.pending_decision.kind)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'targets': [own.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.target(s, 'A', [target.ref]);self.settle(s)
        self.assertEqual('exile', s.state.cards[target.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        self.private(s);self.replay(s, load=True)

    def test_actual_tomeseeker_sacrifices_fresh_creature_token_and_old_trigger_survives_unpaid_reentry(self):
        s = self.session(702166102)
        source = self.add(s.engine, 'Tenacious Tomeseeker', zone='hand')
        raise_alarm = self.add(s.engine, 'Raise the Alarm', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        self.add(s.engine, 'Vedalken Orrery')
        returned = self.add(s.engine, 'Divination', zone='graveyard', ref='bargain-returned-sorcery')
        self.assert_closed(s, source);self.ready(s, {'C': 5, 'U': 3, 'W': 1})
        self.cast_for(s, 'A', raise_alarm, reserve={'C': 4, 'U': 3});self.settle(s)
        token = next(c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and c.controller == 'A')
        self.cast_bargain(s, source, token)
        self.resolve(s);self.target(s, 'A', [returned.ref])
        original = s.state.cards[source.object_id].logical_object_id
        self.cast_for(s, 'A', bounce, [source.ref], reserve={'C': 2, 'U': 1});self.resolve_top(s)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.cast_bargain(s, s.state.cards[source.object_id], chosen=False)
        self.settle(s)
        self.assertNotEqual(original, s.state.cards[source.object_id].logical_object_id)
        self.assertEqual('hand', s.state.cards[returned.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].annotations['declared_cast_costs']['choices'][0]['chosen'])
        self.private(s);self.replay(s, load=True)

    def test_actual_hellkite_bargain_mana_funds_real_activation_and_immediate_haste_attack(self):
        s = self.session(702166103)
        source = self.add(s.engine, 'Realm-Scorcher Hellkite', zone='hand')
        price = self.add(s.engine, 'Sol Ring')
        self.assert_closed(s, source);self.ready(s, {'C': 4, 'R': 2})
        self.cast_bargain(s, source, price)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.replay(s, load=True)
        self.choose_as(s, 'A', mana={'R': 4});self.settle(s)
        self.assertEqual(4, s.state.players['A'].mana_pool['R'])
        self.assertEqual(4, s.engine._numeric_stat(source.object_id, 'power'))
        action = next(row for row in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if row['id'].startswith('activate:' + source.ref + ':'))
        result = s.act('pilot:A', {'action_id': action['id'], 'targets': ['B'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary);self.settle(s)
        self.assertEqual(39, s.state.players['B'].life)
        self.assertEqual(2, s.state.players['A'].mana_pool['R'])
        self.attack(s, [(s.state.cards[source.object_id], 'C')]);self.until(s, lambda s:s.state.players['C'].life == 36)
        self.private(s);self.replay(s, load=True)

    def test_actual_diminisher_witch_creates_controlled_cursed_role_on_opponent_creature(self):
        s = self.session(702166104)
        source = self.add(s.engine, 'Diminisher Witch', zone='hand')
        price = self.add(s.engine, 'Ornithopter')
        target = self.add(s.engine, 'Grizzly Bears', seat='B')
        self.assert_closed(s, source);self.ready(s, {'C': 2, 'U': 1})
        self.cast_bargain(s, source, price)
        self.resolve(s);self.target(s, 'A', [target.ref]);self.settle(s)
        roles = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and c.attached_to == target.object_id]
        self.assertEqual(1, len(roles));self.assertEqual('A', roles[0].controller)
        self.assertEqual(1, s.engine._numeric_stat(s.state.cards[target.object_id].object_id, 'power'))
        self.assertEqual(1, s.engine._numeric_stat(s.state.cards[target.object_id].object_id, 'toughness'))
        self.assertEqual('graveyard', s.state.cards[price.object_id].zone)
        self.private(s);self.replay(s, load=True)

    def test_actual_candy_grapple_bargained_copy_retains_choice_and_unpaid_spell_uses_normal_branch(self):
        for chosen in (True, False):
            with self.subTest(chosen=chosen):
                s = self.session(702166110 + chosen)
                source = self.add(s.engine, 'Candy Grapple', zone='hand')
                price = self.add(s.engine, 'Fervor')
                copy = self.add(s.engine, 'Twincast', zone='hand') if chosen else None
                targets = [self.add(s.engine, 'Gigantosaurus', seat=p, ref='candy-target-' + p) for p in 'BC']
                self.assert_closed(s, source);self.ready(s, {'C': 1, 'B': 1, **({'U': 2} if chosen else {})})
                self.cast_bargain(s, source, price, [targets[0].ref], chosen=chosen, **({'reserve': {'U': 2}} if chosen else {}))
                if copy is not None:
                    original = next(item.ref for item in s.state.stack if item.card_object_id == source.object_id)
                    self.cast(s, 'A', copy, [original]);self.assertEqual('semantic.choice', self.resolve(s).kind)
                    self.choose_as(s, 'A', targets=[targets[1].ref])
                self.settle(s)
                self.assertEqual(5 if chosen else 7, s.engine._numeric_stat(targets[0].object_id, 'power'))
                self.assertEqual(5 if chosen else 10, s.engine._numeric_stat(targets[1].object_id, 'toughness'))
                self.assertEqual(2 if chosen else 1, len(s.engine._current_turn_history('spell_cast')))
                self.finish_turn(s)
                self.assertTrue(all(s.engine._numeric_stat(c.object_id, 'power') == 10 for c in targets))
                self.private(s);self.replay(s, load=True)

    def test_actual_archons_glory_paid_keywords_add_to_buff_and_unpaid_result_still_buffs(self):
        for chosen in (True, False):
            with self.subTest(chosen=chosen):
                s = self.session(702166120 + chosen)
                source = self.add(s.engine, "Archon's Glory", zone='hand')
                price = self.add(s.engine, 'Sol Ring')
                creature = self.add(s.engine, 'Grizzly Bears');creature.acquired_control_turn_count = -1
                self.assert_closed(s, source);self.ready(s, {'W': 1})
                self.cast_bargain(s, source, price, [creature.ref], chosen=chosen);self.settle(s)
                self.assertEqual(4, s.engine._numeric_stat(creature.object_id, 'power'))
                self.attack(s, [(creature, 'B')]);self.until(s, lambda s:s.state.players['B'].life == 36)
                self.assertEqual(44 if chosen else 40, s.state.players['A'].life)
                self.finish_turn(s)
                self.assertEqual(2, s.engine._numeric_stat(creature.object_id, 'power'))
                self.private(s);self.replay(s, load=True)
