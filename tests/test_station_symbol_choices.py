"""Original Station children preserve public target and private/APNAP choices."""
import json
import unittest
import station_symbol_fixture as fixtures
from quorune.record import authoritative_state_hash


class StationSymbolChoiceTests(fixtures.StationSymbolFixture, unittest.TestCase):
    def test_actual_skiff_return_and_seriema_legend_search_keep_scope_then_animate(self):
        for index, name in enumerate(('Rescue Skiff', 'The Seriema')):
            with self.subTest(card=name):
                s = self.session(721661400 + index)
                source = self.add(s.engine, name, zone='hand')
                worker = self.add(s.engine, 'Gigantosaurus')
                if name == 'Rescue Skiff':
                    selected = self.add(s.engine, 'Fervor', zone='graveyard', ref='rescued-haste-enchantment')
                    illegal = self.add(s.engine, 'Grizzly Bears', seat='B', zone='graveyard', ref='foreign-skiff-graveyard')
                    land = self.add(s.engine, 'Island', zone='graveyard', ref='wrong-skiff-land')
                    mana, threshold, stats = {'C': 5, 'W': 1}, 10, (5, 6)
                else:
                    selected = self.add(s.engine, 'Isamaru, Hound of Konda', zone='library', ref='private-seriema-legend')
                    illegal = self.add(s.engine, 'Divination', zone='library', ref='private-seriema-nonlegend')
                    protected = self.add(s.engine, 'Isamaru, Hound of Konda', ref='tapped-legendary-recipient')
                    protected.tapped = True
                    self.add(s.engine, 'Fervor')
                    mana, threshold, stats = {'C': 1, 'W': 2}, 7, (5, 5)
                self.assert_closed(s, source)
                self.ready(s, mana)
                self.cast(s, 'A', source)
                if name == 'Rescue Skiff':
                    self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'semantic.target')
                    for wrong in (illegal, land):
                        before = authoritative_state_hash(s.state)
                        self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'targets': [wrong.ref]}).ok)
                        self.assertEqual(before, authoritative_state_hash(s.state))
                    self.target(s, 'A', [selected.ref])
                    s = self.replay(s, load=True)
                    self.settle(s)
                    self.assertEqual('battlefield', s.state.cards[selected.object_id].zone)
                    self.assertEqual('graveyard', s.state.cards[illegal.object_id].zone)
                else:
                    self.assertEqual('semantic.search', self.resolve(s).kind)
                    for seat in 'BCD':
                        packet = s.packet('pilot:' + seat, full=True)
                        self.assertIsNone(packet['decision'])
                        self.assertNotIn(selected.ref, json.dumps(packet))
                        self.assertNotIn(illegal.ref, json.dumps(packet))
                    before = authoritative_state_hash(s.state)
                    self.assertFalse(s.act('pilot:A', {'action_id': 'choose', 'search_cards': [illegal.ref]}).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                    s = self.replay(s, load=True)
                    shuffles = s.state.players['A'].stats.get('shuffle_count', 0)
                    self.choose(s, search_cards=[selected.ref])
                    self.settle(s)
                    self.assertEqual('hand', s.state.cards[selected.object_id].zone)
                    self.assertEqual(shuffles + 1, s.state.players['A'].stats.get('shuffle_count', 0))
                    self.assertNotIn('indestructible', s.engine._combat_keywords(s.state.cards[protected.object_id]))
                self.animate(s, source, [worker], threshold, stats)
                self.attack(s, [(source, 'B')])
                if name == 'The Seriema':
                    self.assertTrue(s.state.cards[source.object_id].tapped)
                    self.assertNotIn('indestructible', s.engine._combat_keywords(s.state.cards[source.object_id]))
                    self.assertIn('indestructible', s.engine._combat_keywords(s.state.cards[protected.object_id]))
                self.until(s, lambda s: s.state.players['B'].life == 35)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_seedship_and_infinite_station_keep_threshold_attack_children_private_and_counted(self):
        for index, name in enumerate(('Sledge-Class Seedship', 'Infinite Guideline Station')):
            with self.subTest(card=name):
                s = self.session(721661410 + index)
                source = self.add(s.engine, name, zone='hand')
                workers = [self.add(s.engine, 'Gigantosaurus', ref='station-worker-' + str(i)) for i in range(2)]
                self.add(s.engine, 'Fervor')
                if name == 'Sledge-Class Seedship':
                    selected = self.add(s.engine, 'Grizzly Bears', zone='hand', ref='private-seedship-creature')
                    mana, threshold, stats = {'C': 2, 'G': 1}, 7, (4, 5)
                else:
                    self.add(s.engine, 'Fusion Elemental')
                    self.add(s.engine, 'Fusion Elemental', seat='B', ref='foreign-multicolored-permanent')
                    mana, threshold, stats = {'W': 1, 'U': 1, 'B': 1, 'R': 1, 'G': 1}, 12, (7, 15)
                self.assert_closed(s, source)
                self.ready(s, mana)
                self.cast(s, 'A', source)
                self.settle(s)
                if name == 'Infinite Guideline Station':
                    robots = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and c.controller == 'A']
                    self.assertEqual(2, len(robots))
                    for robot in robots:
                        self.assertTrue(robot.tapped)
                        self.assertIn('robot', s.engine._effective_card_data(robot)['type_line'].casefold())
                        self.assertEqual((2, 2), tuple(s.engine._numeric_stat(robot.object_id, stat) for stat in ('power', 'toughness')))
                self.animate(s, source, workers, threshold, stats)
                hand = len(s.state.players['A'].zones['hand'])
                self.attack(s, [(source, 'B')])
                self.assertEqual(1, self.trigger_count(s, source))
                if name == 'Sledge-Class Seedship':
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    for seat in 'BCD':
                        self.assertIsNone(s.packet('pilot:' + seat, full=True)['decision'])
                        self.assertNotIn(selected.ref, json.dumps(s.packet('pilot:' + seat, full=True)))
                    s = self.replay(s, load=True)
                    self.choose(s, card=selected.ref)
                    self.settle(s)
                    self.assertEqual('battlefield', s.state.cards[selected.object_id].zone)
                    self.assertEqual(hand - 1, len(s.state.players['A'].zones['hand']))
                else:
                    self.settle(s)
                    self.assertEqual(hand + 2, len(s.state.players['A'].zones['hand']))
                self.until(s, lambda s: s.state.players['B'].life == 40 - stats[0])
                self.private(s)
                self.replay(s, load=True)

    def test_actual_dirgecraft_apnap_sacrifices_exclude_tokens_then_station_animates(self):
        s = self.session(721661420)
        source = self.add(s.engine, 'Susurian Dirgecraft', zone='hand')
        worker = self.add(s.engine, 'Gigantosaurus')
        sacrifices = {seat: self.add(s.engine, 'Grizzly Bears', seat=seat, ref='dirgecraft-victim-' + seat) for seat in 'BCD'}
        tokens = self.add(s.engine, 'Raise the Alarm', seat='B', zone='hand')
        self.add(s.engine, 'Fervor')
        self.assert_closed(s, source)
        self.ready(s, {'C': 4, 'B': 1})
        s.state.players['B'].mana_pool.update(C=1, W=1)
        self.checkpoint(s)
        self.cast(s, 'B', tokens)
        self.settle(s)
        token = next(c for c in s.state.cards.values() if c.is_token and c.controller == 'B')
        self.cast(s, 'A', source)
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        self.assertEqual(['B'], s.state.pending_decision.actors)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:B', {'action_id': 'choose', 'cards': [token.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.replay(s, load=True)
        for seat in 'BCD':
            self.assertEqual([seat], s.state.pending_decision.actors)
            self.choose(s, seat, cards=[sacrifices[seat].ref])
        self.settle(s)
        for victim in sacrifices.values():
            self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[token.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[worker.object_id].zone)
        self.animate(s, source, [worker], 7, (4, 3))
        self.attack(s, [(source, 'B')])
        self.until(s, lambda s: s.state.players['B'].life == 36)
        self.private(s)
        self.replay(s, load=True)
