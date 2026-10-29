"""Original Cipher object results retain identity, delayed return and copy choices."""
import unittest
import cipher_fixture as fixtures


class CipherObjectResultTests(fixtures.CipherFixture, unittest.TestCase):
    def test_actual_writ_and_midnight_recoveries_resolve_before_encoding_and_again_from_copy(self):
        for index, (name, mana, zone) in enumerate((('Writ of Return', {'C': 3, 'B': 2}, 'battlefield'),
                                                  ('Midnight Recovery', {'C': 3, 'B': 1}, 'hand'))):
            with self.subTest(card=name):
                s = self.session(702990300 + index)
                source = self.add(s.engine, name, zone='hand')
                creature = self.add(s.engine, 'Grizzly Bears')
                creature.acquired_control_turn_count = -1
                first = self.add(s.engine, 'Grizzly Bears', zone='graveyard', ref='cipher-return-first')
                second = self.add(s.engine, 'Grizzly Bears', zone='graveyard', ref='cipher-return-second')
                self.assert_closed(s, source)
                self.ready(s, mana)
                self.cast(s, 'A', source, [first.ref])
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                self.assertEqual(zone, s.state.cards[first.object_id].zone)
                if zone == 'battlefield':
                    self.assertTrue(s.state.cards[first.object_id].tapped)
                self.choose_as(s, 'A', cards=[creature.ref])
                self.settle(s)
                self.attack(s, [(creature, 'D')])
                self.until(s, lambda s: any(i.semantic_key.startswith('builtin:cipher:') for i in s.state.stack))
                self.assertEqual('selection.exile_cast', self.resolve(s).kind)
                self.choose_as(s, 'A', choice='cast', targets=[second.ref])
                self.settle(s)
                self.assertEqual(zone, s.state.cards[second.object_id].zone)
                if zone == 'battlefield':
                    self.assertTrue(s.state.cards[second.object_id].tapped)
                self.assertEqual('exile', s.state.cards[source.object_id].zone)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_voidwalk_delayed_return_and_copy_blink_keep_original_encoding(self):
        s = self.session(702990310)
        source = self.add(s.engine, 'Voidwalk', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears')
        creature.acquired_control_turn_count = -1
        first = self.add(s.engine, 'Grizzly Bears', seat='B', ref='voidwalk-first')
        second = self.add(s.engine, 'Grizzly Bears', seat='C', ref='voidwalk-second')
        self.assert_closed(s, source)
        self.ready(s, {'C': 3, 'U': 1})
        self.cast(s, 'A', source, [first.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual('exile', s.state.cards[first.object_id].zone)
        self.choose_as(s, 'A', cards=[creature.ref])
        self.settle(s)
        self.attack(s, [(creature, 'D')])
        self.until(s, lambda s: any(i.semantic_key.startswith('builtin:cipher:') for i in s.state.stack))
        self.assertEqual('selection.exile_cast', self.resolve(s).kind)
        self.choose_as(s, 'A', choice='cast', targets=[second.ref])
        self.settle(s)
        self.assertEqual('exile', s.state.cards[second.object_id].zone)
        self.until(s, lambda s: s.state.cards[first.object_id].zone == 'battlefield' and s.state.cards[second.object_id].zone == 'battlefield')
        self.assertEqual('battlefield', s.state.cards[first.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[second.object_id].zone)
        self.assertEqual('B', s.state.cards[first.object_id].controller)
        self.assertEqual('C', s.state.cards[second.object_id].controller)
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_hands_of_binding_original_and_copy_tap_distinct_opponents(self):
        s = self.session(702990330)
        source = self.add(s.engine, 'Hands of Binding', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears')
        creature.acquired_control_turn_count = -1
        first = self.add(s.engine, 'Grizzly Bears', seat='B', ref='binding-first')
        second = self.add(s.engine, 'Grizzly Bears', seat='C', ref='binding-second')
        self.assert_closed(s, source)
        self.ready(s, {'C': 1, 'U': 1})
        self.cast(s, 'A', source, [first.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertTrue(s.state.cards[first.object_id].tapped)
        self.choose_as(s, 'A', cards=[creature.ref])
        self.settle(s)
        self.attack(s, [(creature, 'D')])
        self.until(s, lambda s: any(i.semantic_key.startswith('builtin:cipher:') for i in s.state.stack))
        self.assertEqual('selection.exile_cast', self.resolve(s).kind)
        self.choose_as(s, 'A', choice='cast', targets=[second.ref])
        self.settle(s)
        self.assertTrue(s.state.cards[second.object_id].tapped)
        self.finish_turn(s)
        self.assertTrue(s.state.cards[first.object_id].tapped)
        self.finish_turn(s)
        self.assertTrue(s.state.cards[second.object_id].tapped)
        self.assertEqual('exile', s.state.cards[source.object_id].zone)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_stolen_identity_original_and_cipher_copy_create_distinct_artifact_copies(self):
        s = self.session(702990320)
        source = self.add(s.engine, 'Stolen Identity', zone='hand')
        creature = self.add(s.engine, 'Grizzly Bears')
        creature.acquired_control_turn_count = -1
        target = self.add(s.engine, 'Ornithopter', seat='B')
        self.assert_closed(s, source)
        self.ready(s, {'C': 4, 'U': 2})
        self.cast(s, 'A', source, [target.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.choose_as(s, 'A', cards=[creature.ref])
        self.settle(s)
        self.assertEqual(1, sum(c.is_token and c.zone == 'battlefield' for c in s.state.cards.values()))
        self.attack(s, [(creature, 'D')])
        self.until(s, lambda s: any(i.semantic_key.startswith('builtin:cipher:') for i in s.state.stack))
        self.assertEqual('selection.exile_cast', self.resolve(s).kind)
        self.choose_as(s, 'A', choice='cast', targets=[target.ref])
        self.settle(s)
        tokens = [c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield']
        self.assertEqual(2, len(tokens))
        for token in tokens:
            self.assertEqual('A', token.controller)
            self.assertEqual((0, 2), tuple(s.engine._numeric_stat(token.object_id, stat) for stat in ('power', 'toughness')))
            self.assertIn('flying', s.engine._combat_keywords(token))
        self.private(s)
        self.replay(s, load=True)
