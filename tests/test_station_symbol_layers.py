"""Station symbol copies and effects begun before layer6 keep their owners."""
import unittest
import station_symbol_fixture as fixtures
import test_entry_copy_values as copies
from quorune.station import station_cost_choice


class StationSymbolLayerTests(fixtures.StationSymbolFixture, unittest.TestCase):
    choose_copy = copies.EntryCopyValueRuntimeTests.choose_copy

    def test_actual_clone_copies_station_symbols_without_counters_and_humility_restores_after_removal(self):
        s = self.session(721661500)
        original = self.add(s.engine, 'Galvanizing Sawship')
        original.counters['charge'] = 3
        clone = self.add(s.engine, 'Clone', zone='hand')
        worker = self.add(s.engine, 'Gigantosaurus')
        humility = self.add(s.engine, 'Humility', zone='hand')
        removal = self.add(s.engine, 'Disenchant', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B')
        for card in (original, clone, humility):
            self.assert_closed(s, card)
        self.ready(s, {'C': 6, 'U': 1, 'W': 3})
        self.cast_for(s, 'A', clone, reserve={'C': 3, 'W': 3})
        s = self.choose_copy(s, original)
        self.assertEqual('battlefield', s.state.cards[clone.object_id].zone)
        self.assertEqual({}, s.state.cards[clone.object_id].counters)
        data = s.engine._effective_card_data(s.state.cards[clone.object_id])
        self.assertIn('spacecraft', data['type_line'].casefold())
        self.assertNotIn('creature', data['type_line'].casefold())
        self.assertIsNone(data['power'])
        self.assertNotIn('flying', s.engine._combat_keywords(s.state.cards[clone.object_id]))
        self.station(s, clone, worker)
        self.assertEqual(10, s.state.cards[clone.object_id].counters['charge'])
        self.assertEqual((6, 5), tuple(s.engine._numeric_stat(clone.object_id, stat) for stat in ('power', 'toughness')))
        self.cast_for(s, 'A', humility, reserve={'C': 1, 'W': 1})
        self.settle(s)
        for source in (original, clone):
            current = s.state.cards[source.object_id]
            self.assertIn('creature', s.engine._effective_card_data(current)['type_line'].casefold())
            self.assertEqual((1, 1), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
            self.assertNotIn('flying', s.engine._combat_keywords(current))
            self.assertFalse(any(station_cost_choice(a) is not None for a in s.engine._activated_abilities(current)))
        s = self.replay(s, load=True)
        self.cast(s, 'A', removal, [humility.ref])
        self.settle(s)
        for source in (original, clone):
            self.assertEqual((6, 5), tuple(s.engine._numeric_stat(source.object_id, stat) for stat in ('power', 'toughness')))
            self.assertTrue({'flying', 'haste'} <= s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.cast(s, 'B', bounce, [clone.ref])
        self.settle(s)
        self.assertEqual('hand', s.state.cards[clone.object_id].zone)
        self.assertEqual({}, s.state.cards[clone.object_id].counters)
        self.assertNotIn('spacecraft', s.engine._effective_card_data(s.state.cards[clone.object_id])['type_line'].casefold())
        self.assertEqual(3, s.state.cards[original.object_id].counters['charge'])
        self.private(s)
        self.replay(s, load=True)
