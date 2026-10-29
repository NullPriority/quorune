"""Original Forecast bodies and independent normal-card compositions."""
import unittest
import forecast_fixture as fixtures
from quorune.record import authoritative_state_hash


class ForecastResultTests(fixtures.ForecastFixture, unittest.TestCase):
    def test_actual_spirit_forecast_grants_temporary_shadow_and_normal_source_keeps_intrinsic_shadow(self):
        s = self.session(702057101)
        source = self.add(s.engine, 'Spirit en-Dal', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears');body.acquired_control_turn_count = -1
        ring = self.add(s.engine, 'Sol Ring');wastes = self.add(s.engine, 'Wastes')
        plains = [self.add(s.engine, 'Plains', ref='spirit-white-' + str(i)) for i in range(2)]
        self.assert_closed(s, source);self.prepare_forecast(s)
        self.tap_for_mana(s, wastes);self.tap_for_mana(s, plains[0])
        self.activate_forecast(s, source, [body.ref]);self.settle(s)
        self.assertIn('shadow', s.engine._combat_keywords(s.state.cards[body.object_id]))
        self.until(s, lambda s:s.state.phase == 'precombat_main')
        self.tap_for_mana(s, ring);self.tap_for_mana(s, plains[1])
        self.cast(s, 'A', source);self.settle(s)
        self.assertIn('shadow', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.attack(s, [(body, 'B')]);self.until(s, lambda s:s.state.players['B'].life == 38)
        self.finish_turn(s)
        self.assertNotIn('shadow', s.engine._combat_keywords(s.state.cards[body.object_id]))
        self.assertIn('shadow', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.private(s);self.replay(s, load=True)

    def test_actual_plumes_and_piercing_forecast_tap_and_normal_spell_bodies_keep_distinct_predicates(self):
        for index, name in enumerate(('Plumes of Peace', 'Piercing Rays')):
            with self.subTest(card=name):
                s = self.session(702057110 + index)
                source = self.add(s.engine, name, zone='hand')
                tapped = self.add(s.engine, 'Grizzly Bears', seat='B', ref='forecast-tapped-subject')
                enchanted = self.add(s.engine, 'Grizzly Bears', seat='C', ref='normal-aura-subject');enchanted.tapped = True
                whites = [self.add(s.engine, 'Plains', ref=f'tap-white-{index}-{i}') for i in range(2)]
                blues = [self.add(s.engine, 'Island', ref=f'tap-blue-{index}-{i}') for i in range(2)] if index == 0 else []
                generics = [self.add(s.engine, 'Wastes', ref=f'tap-generic-{index}-{i}') for i in range(1 if index == 0 else 4)]
                self.assert_closed(s, source);self.prepare_forecast(s)
                if index == 0:
                    self.tap_for_mana(s, whites[0]);self.tap_for_mana(s, blues[0])
                else:
                    for land in [whites[0], *generics[:2]]:self.tap_for_mana(s, land)
                    action = self.forecast_actions(s, source)[0]
                    before = authoritative_state_hash(s.state)
                    self.assertFalse(s.act('pilot:A', {'action_id': action['id'], 'targets': [enchanted.ref], 'pay': 'auto'}).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                self.activate_forecast(s, source, [tapped.ref]);self.settle(s)
                self.assertTrue(s.state.cards[tapped.object_id].tapped)
                self.until(s, lambda s:s.state.phase == 'precombat_main')
                if index == 0:
                    for land in [whites[1], blues[1], generics[0]]:self.tap_for_mana(s, land)
                    self.cast(s, 'A', source, [enchanted.ref]);self.settle(s)
                    self.assertEqual(enchanted.object_id, s.state.cards[source.object_id].attached_to)
                    self.finish_turn(s)
                    self.assertFalse(s.state.cards[tapped.object_id].tapped)
                    self.finish_turn(s)
                    self.assertTrue(s.state.cards[enchanted.object_id].tapped)
                else:
                    for land in [whites[1], *generics[2:]]:self.tap_for_mana(s, land)
                    self.cast(s, 'A', source, [tapped.ref]);self.settle(s)
                    self.assertEqual('exile', s.state.cards[tapped.object_id].zone)
                self.private(s);self.replay(s, load=True)

    def test_actual_pride_forecast_bird_and_normal_creature_count_other_flyers_from_both_controllers(self):
        s = self.session(702057120)
        source = self.add(s.engine, 'Pride of the Clouds', zone='hand')
        self.add(s.engine, 'Serra Angel', seat='B')
        self.add(s.engine, 'Grizzly Bears', seat='B')
        ring = self.add(s.engine, 'Sol Ring')
        whites = [self.add(s.engine, 'Plains', ref='pride-white-' + str(i)) for i in range(2)]
        blues = [self.add(s.engine, 'Island', ref='pride-blue-' + str(i)) for i in range(2)]
        self.assert_closed(s, source);self.prepare_forecast(s)
        for land in [ring, whites[0], blues[0]]:self.tap_for_mana(s, land)
        self.activate_forecast(s, source);self.settle(s)
        birds = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and c.controller == 'A']
        self.assertEqual(1, len(birds));self.assertEqual(1, s.engine._numeric_stat(birds[0].object_id, 'power'))
        self.assertIn('flying', s.engine._combat_keywords(birds[0]))
        self.until(s, lambda s:s.state.phase == 'precombat_main')
        self.tap_for_mana(s, whites[1]);self.tap_for_mana(s, blues[1])
        self.cast(s, 'A', source);self.settle(s)
        self.assertEqual(3, s.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(3, s.engine._numeric_stat(source.object_id, 'toughness'))
        self.private(s);self.replay(s, load=True)

    def test_actual_sky_hussar_taps_two_current_white_or_blue_creatures_then_normal_entry_untaps_only_own(self):
        s = self.session(702057130)
        source = self.add(s.engine, 'Sky Hussar', zone='hand')
        first = self.add(s.engine, 'Savannah Lions');second = self.add(s.engine, 'Merfolk of the Pearl Trident')
        wrong = self.add(s.engine, 'Grizzly Bears')
        foreign = self.add(s.engine, 'Savannah Lions', seat='B', ref='foreign-sky-price');foreign.tapped = True
        self.add(s.engine, 'Plains');self.add(s.engine, 'Island')
        for i in range(3):self.add(s.engine, 'Wastes', ref='hussar-generic-' + str(i))
        self.assert_closed(s, source);self.prepare_forecast(s)
        first.acquired_control_turn_count = s.state.players['A'].turns_begun
        second.acquired_control_turn_count = s.state.players['A'].turns_begun
        self.checkpoint(s)
        action = self.forecast_actions(s, source)[0]
        refs = set(action['cost_summary']['choose_cost'][0]['legal_refs'])
        self.assertIn(first.ref, refs);self.assertIn(second.ref, refs);self.assertNotIn(wrong.ref, refs);self.assertNotIn(foreign.ref, refs)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': action['id'], 'cost_cards': [first.ref, first.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        hand = len(s.state.players['A'].zones['hand'])
        self.activate_forecast(s, source, cost_cards=[first.ref, second.ref]);self.settle(s)
        self.assertTrue(s.state.cards[first.object_id].tapped);self.assertTrue(s.state.cards[second.object_id].tapped)
        self.assertEqual(hand + 1, len(s.state.players['A'].zones['hand']))
        self.until(s, lambda s:s.state.phase == 'precombat_main')
        self.cast(s, 'A', source);self.settle(s)
        self.assertFalse(s.state.cards[first.object_id].tapped);self.assertFalse(s.state.cards[second.object_id].tapped)
        self.assertTrue(s.state.cards[foreign.object_id].tapped)
        self.assertIn('flying', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.private(s);self.replay(s, load=True)
