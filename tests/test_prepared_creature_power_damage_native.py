"""Original prepared-damage spells through ordinary trusted actions."""
import unittest
import prepared_power_damage_fixture as fixtures


class PreparedPowerDamageNativeTests(fixtures.PreparedPowerDamageFixture, unittest.TestCase):
    def test_actual_fixed_stat_prepared_bites_read_boosted_power_without_return_damage(self):
        for index, name in enumerate(('Rabid Gnaw', 'Clear Shot', "Huatli's Final Strike", 'Ambuscade')):
            with self.subTest(card=name):
                s, spell, first, second = self.board(name, 669100 + index)
                s = self.cast_prepared(s, spell, first, second, resume=index == 0);self.settle(s)
                current = s.state.cards[first.object_id];recipient = s.state.cards[second.object_id]
                self.assertEqual(3, s.engine._numeric_stat(current.object_id, 'power'))
                self.assertEqual(3, recipient.marked_damage);self.assertEqual(0, current.marked_damage)
                self.assertEqual(3 if name == 'Clear Shot' else 2, s.engine._numeric_stat(current.object_id, 'toughness'))
                self.private(s)
                self.finish_turn(s)
                self.assertEqual(2, s.engine._numeric_stat(first.object_id, 'power'))
                self.assertEqual(0, s.state.cards[second.object_id].marked_damage)
                if index == 0:self.replay(s, load=True)

    def test_actual_counter_prepared_bites_keep_counter_and_use_replaced_or_prohibited_result(self):
        for index, (name, control, expected) in enumerate((
            ("Hunter's Edge", None, 1), ('Felling Blow', 'Hardened Scales', 2),
            ("Domri's Ambush", 'Solemnity', 0), ('Knockout Maneuver', None, 1),
        )):
            with self.subTest(card=name, control=control):
                s, spell, first, second = self.board(name, 669110 + index)
                if control:
                    permanent = self.add(s.engine, control);self.assert_closed(s, permanent);self.checkpoint(s)
                s = self.cast_prepared(s, spell, first, second);self.settle(s)
                self.assertEqual(expected, s.state.cards[first.object_id].counters.get('+1/+1', 0))
                self.assertEqual(2 + expected, s.state.cards[second.object_id].marked_damage)
                self.assertEqual(0, s.state.cards[first.object_id].marked_damage)
                self.private(s);self.finish_turn(s)
                self.assertEqual(2 + expected, s.engine._numeric_stat(first.object_id, 'power'))
                if control == 'Hardened Scales':self.replay(s, load=True)

    def test_actual_keyword_preparation_keeps_trample_noncombat_and_expires(self):
        for index, name in enumerate(("Nature's Way", 'Diplomatic Relations')):
            with self.subTest(card=name):
                s, spell, first, second = self.board(name, 669120 + index, recipient='Merfolk of the Pearl Trident')
                life = s.state.players['B'].life
                s = self.cast_prepared(s, spell, first, second);self.settle(s)
                self.assertEqual('graveyard', s.state.cards[second.object_id].zone)
                self.assertEqual(life, s.state.players['B'].life)
                keywords = s.engine._combat_keywords(s.state.cards[first.object_id])
                self.assertIn('vigilance', keywords)
                self.assertEqual(name == "Nature's Way", 'trample' in keywords)
                self.assertEqual(2 if name == "Nature's Way" else 3, s.engine._numeric_stat(first.object_id, 'power'))
                self.private(s);self.finish_turn(s)
                self.assertNotIn('trample', s.engine._combat_keywords(s.state.cards[first.object_id]))
                self.assertNotIn('vigilance', s.engine._combat_keywords(s.state.cards[first.object_id]))
                self.assertEqual(2, s.engine._numeric_stat(first.object_id, 'power'))
                if index == 0:self.replay(s, load=True)

    def test_actual_prepared_damage_uses_creature_lifelink_deathtouch_and_prevention(self):
        for index, prevent in enumerate((False, True)):
            with self.subTest(prevent=prevent):
                s, spell, first, second = self.board('Rabid Gnaw', 669130 + index, fighter='Vampire Nighthawk')
                shield = self.add(s.engine, 'Healing Salve', seat='B', zone='hand') if prevent else None
                if shield:self.add(s.engine, 'Plains', seat='B')
                self.checkpoint(s);life = s.state.players['A'].life
                s = self.cast_prepared(s, spell, first, second)
                if shield:
                    self.priority(s, 'B');row = self.action(s, 'B', shield)
                    result = s.act('pilot:B', {'action_id': row['id'], 'modes': ['mode_2'], 'targets': [second.ref], 'pay': 'auto'})
                    self.assertTrue(result.ok, result.summary);self.resolve_top(s)
                self.settle(s)
                self.assertEqual(life + (0 if prevent else 3), s.state.players['A'].life)
                self.assertEqual('battlefield' if prevent else 'graveyard', s.state.cards[second.object_id].zone)
                self.assertEqual(0, s.state.cards[first.object_id].marked_damage)
                self.private(s);self.replay(s, load=True)

    def test_actual_prepared_damage_preserves_partial_target_legality_and_incarnations(self):
        for index, invalid_source in enumerate((False, True)):
            with self.subTest(invalid_source=invalid_source):
                s, spell, first, second = self.board("Hunter's Edge", 669140 + index)
                if invalid_source:
                    self.add(s.engine, 'Vedalken Orrery');self.add(s.engine, 'Forest');self.add(s.engine, 'Wastes')
                bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand');self.add(s.engine, 'Island', seat='B');self.checkpoint(s)
                s = self.cast_prepared(s, spell, first, second)
                self.cast(s, 'B', bounce, [first.ref if invalid_source else second.ref]);self.resolve_top(s)
                if invalid_source:
                    self.cast(s, 'A', first);self.resolve_top(s)
                    self.assertEqual('battlefield', s.state.cards[first.object_id].zone)
                self.settle(s)
                self.assertEqual(0 if invalid_source else 1, s.state.cards[first.object_id].counters.get('+1/+1', 0))
                self.assertEqual(0, s.state.cards[second.object_id].marked_damage)
                self.private(s);self.replay(s, load=True)

    def test_actual_hunters_mark_blue_planeswalker_discount_and_counterproof_body(self):
        s, spell, first, second = self.board("Hunter's Mark", 669150, recipient='Jace Beleren')
        second.counters['loyalty'] = 3
        second.annotations['loyalty_initialized'] = True
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand')
        for index in range(2):self.add(s.engine, 'Island', seat='B', ref='mark-counter-island-' + str(index))
        self.checkpoint(s);s = self.cast_prepared(s, spell, first, second)
        self.assertEqual(3, s.state.players['A'].mana_pool.get('C', 0))
        self.assertEqual(0, s.state.players['A'].mana_pool.get('G', 0))
        item = next(item for item in s.state.stack if item.card_object_id == spell.object_id)
        self.cast(s, 'B', counter, [item.ref]);self.resolve_top(s)
        self.assertEqual('stack', s.state.cards[spell.object_id].zone)
        self.settle(s)
        self.assertEqual(3, s.engine._numeric_stat(first.object_id, 'power'))
        self.assertEqual('graveyard', s.state.cards[second.object_id].zone)
        self.private(s);self.replay(s, load=True)

    def test_actual_colossal_collision_bite_and_independent_basic_landcycling(self):
        s, spell, first, second = self.board('Colossal Collision', 669160)
        s = self.cast_prepared(s, spell, first, second);self.settle(s)
        self.assertEqual(1, s.state.cards[first.object_id].counters.get('+1/+1'))
        self.assertEqual(3, s.state.cards[second.object_id].marked_damage)
        self.private(s);self.replay(s, load=True)
        s = self.session(669161)
        source = self.add(s.engine, 'Colossal Collision', zone='hand')
        land = self.add(s.engine, 'Forest', zone='library', ref='cycling-private-forest')
        self.assert_closed(s, source);self.ready(s, {'C': 2})
        self.activate(s, 'A', source)
        self.until(s, lambda s: getattr(s.state.pending_decision, 'kind', None) == 'semantic.search')
        s = self.replay(s, load=True)
        result = s.act('pilot:A', {'action_id': 'choose', 'search_cards': [land.ref]});self.assertTrue(result.ok, result.summary)
        self.settle(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual('hand', s.state.cards[land.object_id].zone)
        self.private(s);self.replay(s, load=True)
