"""Original Casualty bodies preserve independent private and public results."""
import unittest
import casualty_fixture as fixtures


class CasualtyResultTests(fixtures.CasualtyFixture, unittest.TestCase):
    def test_actual_cut_profits_copy_retains_x_and_both_draw_life_results(self):
        s = self.session(702153101)
        source = self.add(s.engine, 'Cut of the Profits', zone='hand')
        price = self.add(s.engine, 'Gigantosaurus')
        self.assert_closed(s, source);self.ready(s, {'C': 2, 'B': 2})
        before = len(s.state.players['A'].zones['hand']) - 1
        self.cast_casualty(s, source, price, x=2)
        self.settle(s)
        self.assertEqual(before + 4, len(s.state.players['A'].zones['hand']))
        self.assertEqual(36, s.state.players['A'].life)
        self.assertEqual(1, len(s.engine._current_turn_history('spell_cast')))
        self.private(s);self.replay(s, load=True)

    def test_actual_chat_and_shipment_copy_and_original_make_independent_private_choices(self):
        for index, (name, mana) in enumerate((('A Little Chat', {'C': 1, 'U': 1}), ('Illicit Shipment', {'C': 3, 'B': 2}))):
            with self.subTest(card=name):
                s = self.session(702153110 + index)
                source = self.add(s.engine, name, zone='hand')
                price = self.add(s.engine, 'Gigantosaurus')
                cards = [self.add(s.engine, 'Divination', zone='library', ref=f'casualty-private-library-{index}-{i}') for i in range(4)]
                library = s.state.players['A'].zones['library']
                for card in cards:library.remove(card.object_id)
                library.extend(c.object_id for c in reversed(cards))
                self.assert_closed(s, source);self.ready(s, mana)
                before = len(s.state.players['A'].zones['hand']) - 1
                self.cast_casualty(s, source, price)
                self.resolve(s)
                selected = []
                for chosen in (cards[0], cards[2]):
                    kind = s.state.pending_decision.kind
                    self.assertIn(kind, ('semantic.choice', 'semantic.search'))
                    for seat in 'BCD':self.assertNotIn(chosen.ref, str(s.packet('pilot:' + seat, full=True)))
                    s = self.replay(s, load=True)
                    field = 'search_cards' if kind == 'semantic.search' else 'cards'
                    accepted = s.act('pilot:A', {'action_id': 'choose', field: [chosen.ref]})
                    self.assertTrue(accepted.ok, accepted.summary);selected.append(chosen.object_id)
                    if chosen is cards[0]:self.resolve(s)
                self.settle(s)
                self.assertEqual(before + 2, len(s.state.players['A'].zones['hand']))
                self.assertTrue(all(s.state.cards[oid].zone == 'hand' for oid in selected))
                self.assertEqual(1, len(s.engine._current_turn_history('spell_cast')))
                self.private(s);self.replay(s, load=True)

    def test_actual_rooftop_nuisance_copy_taps_fresh_recipient_and_both_draw_tails_resolve(self):
        s = self.session(702153120)
        source = self.add(s.engine, 'Rooftop Nuisance', zone='hand')
        price = self.add(s.engine, 'Grizzly Bears')
        targets = [self.add(s.engine, 'Grizzly Bears', seat=p, ref='rooftop-casualty-' + p) for p in 'BC']
        self.assert_closed(s, source);self.ready(s, {'C': 2, 'U': 1})
        before = len(s.state.players['A'].zones['hand']) - 1
        self.cast_casualty(s, source, price, [targets[0].ref])
        self.assertEqual('semantic.storm', self.resolve(s).kind)
        self.choose_copy(s, [targets[1].ref]);self.settle(s)
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in targets))
        self.assertEqual(before + 2, len(s.state.players['A'].zones['hand']))
        self.finish_turn(s)
        self.assertEqual('B', s.state.active_player)
        self.assertTrue(s.state.cards[targets[0].object_id].tapped)
        self.finish_turn(s)
        self.assertEqual('C', s.state.active_player)
        self.assertTrue(s.state.cards[targets[1].object_id].tapped)
        self.private(s);self.replay(s, load=True)

    def test_actual_rob_archives_copy_exiles_four_cards_and_offers_actual_same_turn_cast(self):
        s = self.session(702153130)
        source = self.add(s.engine, 'Rob the Archives', zone='hand')
        price = self.add(s.engine, 'Grizzly Bears')
        cards = [self.add(s.engine, 'Divination', zone='library', ref='casualty-impulse-' + str(i)) for i in range(4)]
        library = s.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(c.object_id for c in reversed(cards))
        self.assert_closed(s, source);self.ready(s, {'C': 3, 'R': 1, 'U': 1})
        self.cast_casualty(s, source, price);self.settle(s)
        self.assertTrue(all(s.state.cards[c.object_id].zone == 'exile' for c in cards))
        offered = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(row.get('card') == cards[0].ref for row in offered))
        before = len(s.state.players['A'].zones['hand'])
        self.cast(s, 'A', s.state.cards[cards[0].object_id]);self.settle(s)
        self.assertEqual(before + 2, len(s.state.players['A'].zones['hand']))
        self.assertEqual(2, len(s.engine._current_turn_history('spell_cast')))
        self.private(s);self.replay(s, load=True)
