"""Complete original spells and the separately admitted Persist source result."""
import unittest
from unittest.mock import patch
import creature_power_multiple_fixture as fixtures
from quorune.record import authoritative_state_hash


class CreaturePowerMultipleNativeTests(fixtures.CreaturePowerMultipleFixture, unittest.TestCase):
    def test_actual_three_power_multiple_spells_keep_distinct_discount_predicates(self):
        for index, name in enumerate(('Punishing Punch', "Animist's Might", 'Thing Swing')):
            for discounted in (False, True):
                with self.subTest(card=name, discounted=discounted):
                    fighter = ('Isamaru, Hound of Konda' if name == "Animist's Might" else 'Changeling Outcast') if discounted and name != 'Punishing Punch' else 'Grizzly Bears'
                    walker = name == "Animist's Might" and discounted
                    s, spell, source, target = self.multiple_board(name, 671100 + index * 2 + int(discounted), fighter=fighter, recipient='Jace Beleren' if walker else 'Wall of Stone', discount=discounted)
                    self.assert_closed(s, source);self.cast_multiple(s, spell, source, target)
                    self.assertEqual(2 if discounted else 0, s.state.players['A'].mana_pool.get('C', 0))
                    self.assertEqual(0, s.state.players['A'].mana_pool.get('G', 0))
                    self.settle(s)
                    expected = 2 if fighter == 'Changeling Outcast' else 4
                    if walker:self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
                    else:self.assertEqual(expected, s.state.cards[target.object_id].marked_damage)
                    event = next(e for e in reversed(s.state.events) if e.code == 'effect.creature_power_damage')
                    self.assertEqual([expected], event.details['dealt_amounts'])
                    self.assertEqual(0, s.state.cards[source.object_id].marked_damage)
                    self.private(s)
                    if discounted:self.replay(s, load=True)

    def test_actual_power_multiple_reads_response_power_and_preserves_creature_lifelink(self):
        s, spell, source, target = self.multiple_board('Punishing Punch', 671110, fighter='Vampire Nighthawk')
        growth = self.add(s.engine, 'Giant Growth', zone='hand');forest = self.add(s.engine, 'Forest')
        self.checkpoint(s);life = s.state.players['A'].life
        self.cast_multiple(s, spell, source, target)
        self.tap_mana(s, forest);self.cast(s, 'A', growth, [source.ref]);self.resolve_top(s)
        self.assertEqual(5, s.engine._numeric_stat(source.object_id, 'power'))
        s = self.replay(s, load=True);self.settle(s)
        self.assertEqual(life + 10, s.state.players['A'].life)
        self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
        self.assertEqual(0, s.state.cards[source.object_id].marked_damage)
        self.private(s);self.replay(s, load=True)

    def test_actual_power_multiple_zero_power_and_returned_source_are_distinct_no_damage_cases(self):
        for index, returned in enumerate((False, True)):
            with self.subTest(returned=returned):
                s, spell, source, target = self.multiple_board('Punishing Punch', 671120 + index, fighter='Grizzly Bears' if returned else 'Ornithopter')
                if returned:
                    bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand');self.add(s.engine, 'Island', seat='B')
                    self.add(s.engine, 'Vedalken Orrery');self.add(s.engine, 'Forest');self.add(s.engine, 'Wastes')
                self.checkpoint(s);identity = s.state.cards[source.object_id].logical_object_id
                self.cast_multiple(s, spell, source, target)
                if returned:
                    self.cast(s, 'B', bounce, [source.ref]);self.resolve_top(s)
                    self.cast(s, 'A', source);self.resolve_top(s)
                    self.assertNotEqual(identity, s.state.cards[source.object_id].logical_object_id)
                self.settle(s)
                self.assertEqual(0, s.state.cards[target.object_id].marked_damage)
                self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
                self.private(s)
                if returned:self.replay(s, load=True)

    def test_actual_multiple_multiplier_omission_mutant_changes_observed_damage(self):
        s, spell, source, target = self.multiple_board('Punishing Punch', 671130)
        self.cast_multiple(s, spell, source, target)
        import quorune.creature_power_damage_model as owner
        with patch.object(owner, 'creature_power_damage_multiplier', return_value=1):self.settle(s)
        with self.assertRaises(AssertionError):self.assertEqual(4, s.state.cards[target.object_id].marked_damage)
        self.assertEqual(2, s.state.cards[target.object_id].marked_damage)

    def test_actual_aerie_ouphes_persist_new_source_does_not_replace_sacrificed_power(self):
        s = self.session(671140)
        source = self.add(s.engine, 'Aerie Ouphes', zone='hand')
        target = self.add(s.engine, 'Serra Angel', seat='B', ref='aerie-flying-recipient')
        resting = self.add(s.engine, 'Grizzly Bears', seat='B', ref='aerie-nonflying-recipient')
        self.assert_closed(s, source);self.ready(s, {'C': 4, 'G': 1})
        self.cast(s, 'A', source);self.settle(s)
        identity = s.state.cards[source.object_id].logical_object_id
        self.priority(s, 'A');row = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':'))
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': row['id'], 'targets': [resting.ref]}).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        self.activate_counter_source(s, source, [target.ref]);self.resolve_top(s)
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.assertNotEqual(identity, s.state.cards[source.object_id].logical_object_id)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('-1/-1'))
        self.assertEqual(2, s.engine._numeric_stat(source.object_id, 'power'))
        s = self.replay(s, load=True);self.settle(s)
        self.assertEqual(3, s.state.cards[target.object_id].marked_damage)
        self.activate_counter_source(s, source, [target.ref]);self.settle(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[target.object_id].zone)
        self.assertEqual(0, s.state.cards[resting.object_id].marked_damage)
        self.private(s);self.replay(s, load=True)
