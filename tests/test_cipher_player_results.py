"""Original Cipher player results retain fresh copy targets and private choices."""
import unittest
import cipher_fixture as fixtures


class CipherPlayerResultTests(fixtures.CipherFixture, unittest.TestCase):
    def test_actual_delusions_and_shadow_slice_original_and_combat_copy_use_new_player_target(self):
        for index, (name, mana) in enumerate((('Paranoid Delusions', {'U': 1, 'B': 1}), ('Shadow Slice', {'C': 4, 'B': 1}))):
            with self.subTest(card=name):
                s = self.session(702990200 + index)
                source = self.add(s.engine, name, zone='hand')
                creature = self.add(s.engine, 'Grizzly Bears')
                creature.acquired_control_turn_count = -1
                self.assert_closed(s, source)
                self.ready(s, mana)
                before = {seat: (len(s.state.players[seat].zones['library']), s.state.players[seat].life) for seat in 'BC'}
                self.cast(s, 'A', source, ['B'])
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                if name == 'Paranoid Delusions':
                    self.assertEqual(before['B'][0] - 3, len(s.state.players['B'].zones['library']))
                else:
                    self.assertEqual(before['B'][1] - 3, s.state.players['B'].life)
                self.choose_as(s, 'A', cards=[creature.ref])
                self.settle(s)
                self.attack(s, [(creature, 'D')])
                self.until(s, lambda s: any(i.semantic_key.startswith('builtin:cipher:') for i in s.state.stack))
                self.assertEqual('selection.exile_cast', self.resolve(s).kind)
                self.choose_as(s, 'A', choice='cast', targets=['C'])
                self.settle(s)
                if name == 'Paranoid Delusions':
                    self.assertEqual(before['C'][0] - 3, len(s.state.players['C'].zones['library']))
                    self.assertEqual(before['B'][0] - 3, len(s.state.players['B'].zones['library']))
                else:
                    self.assertEqual(before['C'][1] - 3, s.state.players['C'].life)
                    self.assertEqual(before['B'][1] - 3, s.state.players['B'].life)
                self.assertEqual('exile', s.state.cards[source.object_id].zone)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_mental_vapors_private_discards_belong_to_each_fresh_target(self):
        s = self.session(702990210)
        source = self.add(s.engine, 'Mental Vapors', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears')
        creature.acquired_control_turn_count = -1
        self.assert_closed(s, source)
        self.ready(s, {'C': 3, 'B': 1})
        counts = {seat: len(s.state.players[seat].zones['hand']) for seat in 'BC'}
        self.cast(s, 'A', source, ['B'])
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        chosen = s.state.cards[s.state.players['B'].zones['hand'][0]].ref
        s = self.replay(s, load=True)
        self.choose_as(s, 'B', cards=[chosen])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_as(s, 'A', cards=[creature.ref])
        self.settle(s)
        self.assertEqual(counts['B'] - 1, len(s.state.players['B'].zones['hand']))
        self.attack(s, [(creature, 'D')])
        self.until(s, lambda s: any(i.semantic_key.startswith('builtin:cipher:') for i in s.state.stack))
        self.assertEqual('selection.exile_cast', self.resolve(s).kind)
        self.choose_as(s, 'A', choice='cast', targets=['C'])
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        self.assertEqual(['C'], s.state.pending_decision.actors)
        chosen = s.state.cards[s.state.players['C'].zones['hand'][0]].ref
        self.choose_as(s, 'C', cards=[chosen])
        self.settle(s)
        self.assertEqual(counts['C'] - 1, len(s.state.players['C'].zones['hand']))
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_undercity_plague_orders_life_discard_sacrifice_before_encoding_and_copies(self):
        s = self.session(702990220)
        source = self.add(s.engine, 'Undercity Plague', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears')
        creature.acquired_control_turn_count = -1
        victims = {seat: self.add(s.engine, 'Plains', seat=seat, ref='plague-land-' + seat) for seat in 'BC'}
        self.assert_closed(s, source)
        self.ready(s, {'C': 4, 'B': 2})
        self.cast(s, 'A', source, ['B'])
        for seat in 'BC':
            self.assertEqual('choice.apnap', self.resolve(s).kind)
            self.assertEqual(39, s.state.players[seat].life)
            chosen = s.state.cards[s.state.players[seat].zones['hand'][0]].ref
            self.choose_as(s, seat, cards=[chosen])
            self.assertEqual('choice.apnap', self.resolve(s).kind)
            self.choose_as(s, seat, cards=[victims[seat].ref])
            if seat == 'B':
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.choose_as(s, 'A', cards=[creature.ref])
                self.settle(s)
                self.attack(s, [(creature, 'D')])
                self.until(s, lambda s: any(i.semantic_key.startswith('builtin:cipher:') for i in s.state.stack))
                self.assertEqual('selection.exile_cast', self.resolve(s).kind)
                self.choose_as(s, 'A', choice='cast', targets=['C'])
        self.settle(s)
        self.assertTrue(all(s.state.cards[card.object_id].zone == 'graveyard' for card in victims.values()))
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.private(s)
        self.replay(s, load=True)
