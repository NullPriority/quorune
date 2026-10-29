"""Original Spacecraft entry bodies resolve before their later Station animation."""
import json
import unittest
import station_symbol_fixture as fixtures


class StationSymbolEntryTests(fixtures.StationSymbolFixture, unittest.TestCase):
    def test_actual_rammer_greenhouse_sweeper_and_scanship_keep_complete_entry_results(self):
        cases = (('Wedgelight Rammer', {'C': 3, 'W': 1}, 9, (3, 4), ('flying', 'first strike')),
                 ('Atmospheric Greenhouse', {'C': 4, 'G': 1}, 8, (5, 4), ('flying', 'trample')),
                 ('Wurmwall Sweeper', {'C': 2}, 4, (2, 2), ('flying',)),
                 ('Uthros Scanship', {'C': 3, 'U': 1}, 8, (4, 4), ('flying',)))
        for index, (name, mana, threshold, stats, keywords) in enumerate(cases):
            with self.subTest(card=name):
                s = self.session(721661300 + index)
                source = self.add(s.engine, name, zone='hand')
                worker = self.add(s.engine, 'Gigantosaurus')
                own = self.add(s.engine, 'Grizzly Bears')
                foreign = self.add(s.engine, 'Grizzly Bears', seat='B', ref='foreign-entry-bear')
                self.add(s.engine, 'Fervor')
                if name == 'Wurmwall Sweeper':
                    top = self.add(s.engine, 'Divination', zone='library', ref='private-station-surveil-top')
                    grave = self.add(s.engine, 'Giant Growth', zone='library', ref='private-station-surveil-grave')
                    library = s.state.players['A'].zones['library']
                    library.remove(top.object_id)
                    library.remove(grave.object_id)
                    library.extend([grave.object_id, top.object_id])
                self.assert_closed(s, source)
                self.ready(s, mana)
                self.cast(s, 'A', source)
                hand = len(s.state.players['A'].zones['hand'])
                if name == 'Wurmwall Sweeper':
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    for seat in 'BCD':
                        packet = s.packet('pilot:' + seat, full=True)
                        self.assertIsNone(packet['decision'])
                        self.assertNotIn(top.ref, json.dumps(packet))
                        self.assertNotIn(grave.ref, json.dumps(packet))
                    s = self.replay(s, load=True)
                    self.choose(s, cards={'top': [top.ref], 'graveyard': [grave.ref]})
                    self.settle(s)
                    self.assertEqual('graveyard', s.state.cards[grave.object_id].zone)
                    self.assertEqual(top.object_id, s.state.players['A'].zones['library'][-1])
                elif name == 'Uthros Scanship':
                    self.assertEqual('choice.apnap', self.resolve(s).kind)
                    self.assertEqual(['A'], s.state.pending_decision.actors)
                    self.assertEqual(hand + 2, len(s.state.players['A'].zones['hand']))
                    discard = next(s.state.cards[oid] for oid in s.state.players['A'].zones['hand'] if s.state.cards[oid].ref != 'private-recipient-hand')
                    for seat in 'BCD':
                        self.assertIsNone(s.packet('pilot:' + seat, full=True)['decision'])
                    s = self.replay(s, load=True)
                    self.choose(s, cards=[discard.ref])
                    self.settle(s)
                    self.assertEqual(hand + 1, len(s.state.players['A'].zones['hand']))
                    self.assertEqual('graveyard', s.state.cards[discard.object_id].zone)
                else:
                    self.settle(s)
                self.assertNotIn('creature', s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'].casefold())
                if name == 'Wedgelight Rammer':
                    tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
                    self.assertEqual(1, len(tokens))
                    robot = tokens[0]
                    self.assertEqual('A', robot.controller)
                    self.assertEqual((2, 2), tuple(s.engine._numeric_stat(robot.object_id, stat) for stat in ('power', 'toughness')))
                    self.assertIn('robot', s.engine._effective_card_data(robot)['type_line'].casefold())
                    self.assertIn('artifact', s.engine._effective_card_data(robot)['type_line'].casefold())
                    self.assertEqual([], s.engine._effective_card_data(robot)['colors'])
                if name == 'Atmospheric Greenhouse':
                    self.assertEqual(1, s.state.cards[worker.object_id].counters['+1/+1'])
                    self.assertEqual(1, s.state.cards[own.object_id].counters['+1/+1'])
                    self.assertEqual({}, s.state.cards[foreign.object_id].counters)
                    self.assertEqual({}, s.state.cards[source.object_id].counters)
                self.animate(s, source, [worker], threshold, stats, keywords)
                self.attack(s, [(source, 'B')])
                self.until(s, lambda s: s.state.players['B'].life == 40 - stats[0])
                if name == 'Wedgelight Rammer':
                    self.until(s, lambda s: s.state.phase_index >= 9)
                    self.assertEqual(37, s.state.players['B'].life)
                self.private(s)
                self.replay(s, load=True)
