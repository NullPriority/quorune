"""Complete pinned originals, including their independent companion abilities."""
import unittest
import source_counter_pronoun_fixture as fixtures
from quorune.record import authoritative_state_hash


class SourceCounterPronounNativeTests(fixtures.SourceCounterPronounFixture, unittest.TestCase):
    def test_actual_time_bomb_upkeep_then_sacrifice_damages_every_player_and_creature(self):
        for index, age in enumerate((False, True)):
            with self.subTest(upkeep=age):
                s = self.session(670100 + index)
                source = self.add(s.engine, 'Time Bomb', zone='hand')
                ring = self.add(s.engine, 'Sol Ring')
                small = self.add(s.engine, 'Merfolk of the Pearl Trident', seat='B', ref='bomb-small-creature')
                large = self.add(s.engine, 'Grizzly Bears', seat='C', ref='bomb-large-creature')
                self.assert_closed(s, source);self.ready(s, {'C': 4})
                self.cast(s, 'A', source);self.settle(s)
                if age:
                    self.step(s, 'A', 'beginning', 'upkeep');self.settle(s)
                self.assertEqual(int(age), s.state.cards[source.object_id].counters.get('time', 0))
                self.tap_mana(s, ring);life = {seat:s.state.players[seat].life for seat in 'ABCD'}
                s = self.activate_counter_source(s, source, resume=age)
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertFalse(s.state.cards[source.object_id].counters)
                self.settle(s)
                for seat in 'ABCD':self.assertEqual(life[seat] - int(age), s.state.players[seat].life)
                self.assertEqual('graveyard' if age else 'battlefield', s.state.cards[small.object_id].zone)
                self.assertEqual(int(age), s.state.cards[large.object_id].marked_damage)
                self.private(s)
                if age:self.replay(s, load=True)

    def test_actual_magma_mine_cost_departure_count_survives_regrowth_and_new_source(self):
        s = self.session(670110)
        source = self.add(s.engine, 'Magma Mine', zone='hand')
        regrowth = self.add(s.engine, 'Regrowth', zone='hand')
        self.add(s.engine, 'Vedalken Orrery')
        ring = self.add(s.engine, 'Sol Ring');forest = self.add(s.engine, 'Forest');wastes = self.add(s.engine, 'Wastes')
        self.assert_closed(s, source);self.ready(s, {'C': 9})
        self.cast(s, 'A', source);self.settle(s)
        for _ in range(2):self.activate_counter_source(s, source, number=1);self.settle(s)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('pressure'))
        incarnation = s.state.cards[source.object_id].logical_object_id
        life = {seat:s.state.players[seat].life for seat in 'ABCD'}
        s = self.activate_counter_source(s, source, ['D'], number=2, resume=True)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
        for mana_source in (ring, forest, wastes):self.tap_mana(s, mana_source)
        self.cast(s, 'A', regrowth, [source.ref]);self.resolve_top(s)
        self.cast(s, 'A', source);self.resolve_top(s)
        self.assertNotEqual(incarnation, s.state.cards[source.object_id].logical_object_id)
        self.assertFalse(s.state.cards[source.object_id].counters)
        self.settle(s)
        self.assertEqual(life['D'] - 2, s.state.players['D'].life)
        for seat in 'ABC':self.assertEqual(life[seat], s.state.players[seat].life)
        self.private(s);self.replay(s, load=True)

    def test_actual_heliophial_sunburst_counts_spent_colors_and_zero_before_source_sacrifice(self):
        for index, (mana, expected) in enumerate((({'W': 1, 'U': 1, 'B': 1, 'C': 2}, 3), ({'C': 5}, 0))):
            with self.subTest(colors=expected):
                s = self.session(670120 + index)
                source = self.add(s.engine, 'Heliophial', zone='hand');ring = self.add(s.engine, 'Sol Ring')
                self.assert_closed(s, source);self.ready(s, mana)
                self.cast(s, 'A', source);self.settle(s)
                self.assertEqual(expected, s.state.cards[source.object_id].counters.get('charge', 0))
                life = s.state.players['C'].life;self.tap_mana(s, ring)
                s = self.activate_counter_source(s, source, ['C'], resume=index == 0)
                self.settle(s)
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertEqual(life - expected, s.state.players['C'].life)
                self.private(s)
                if index == 0:self.replay(s, load=True)

    def test_actual_shrine_counts_own_upkeep_and_red_cast_but_not_foreign_or_blue_casts(self):
        s = self.session(670130)
        source = self.add(s.engine, 'Shrine of Burning Rage', zone='hand')
        red = self.add(s.engine, 'Shock', zone='hand');foreign = self.add(s.engine, 'Shock', seat='B', zone='hand', ref='foreign-red-spell')
        blue = self.add(s.engine, 'Unsummon', zone='hand')
        returned = self.add(s.engine, 'Merfolk of the Pearl Trident', ref='shrine-nonred-subject')
        ring = self.add(s.engine, 'Sol Ring');wastes = self.add(s.engine, 'Wastes')
        mountain = self.add(s.engine, 'Mountain');self.add(s.engine, 'Mountain', seat='B', ref='foreign-red-land')
        island = self.add(s.engine, 'Island')
        self.assert_closed(s, source);self.ready(s, {'C': 2})
        self.cast(s, 'A', source);self.settle(s)
        self.step(s, 'A', 'beginning', 'upkeep');self.settle(s)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('charge'))
        self.tap_mana(s, mountain);self.cast(s, 'A', red, ['D']);self.settle(s)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('charge'))
        self.cast(s, 'B', foreign, ['C']);self.settle(s)
        self.tap_mana(s, island);self.cast(s, 'A', blue, [returned.ref]);self.settle(s)
        self.assertEqual(2, s.state.cards[source.object_id].counters.get('charge'))
        self.tap_mana(s, ring);self.tap_mana(s, wastes)
        life = s.state.players['B'].life
        s = self.activate_counter_source(s, source, ['B'], resume=True);self.settle(s)
        self.assertEqual(life - 2, s.state.players['B'].life)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.private(s);self.replay(s, load=True)

    def test_actual_javelineer_paid_upkeep_targets_only_combat_creature_then_declined_upkeep(self):
        s = self.session(670140)
        source = self.add(s.engine, 'Kjeldoran Javelineer', zone='hand');ring = self.add(s.engine, 'Sol Ring')
        attacker = self.add(s.engine, 'Merfolk of the Pearl Trident', seat='B', ref='javelineer-combat-target')
        resting = self.add(s.engine, 'Grizzly Bears', seat='B', ref='javelineer-resting-creature')
        attacker.acquired_control_turn_count = -1
        self.assert_closed(s, source);self.ready(s, {'W': 1})
        self.cast(s, 'A', source);self.settle(s)
        s.state.cards[source.object_id].acquired_control_turn_count = -1;self.checkpoint(s)
        self.step(s, 'A', 'beginning', 'upkeep');self.tap_mana(s, ring)
        self.upkeep_choice(s, pay=True);self.settle(s)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('age'))
        self.step(s, 'B', 'combat', 'beginning_combat')
        self.until(s, lambda s: getattr(s.state.pending_decision, 'kind', None) == 'combat.attackers')
        result = s.act('pilot:B', {'action_id': 'attack', 'attackers': {attacker.ref: 'A'}});self.assertTrue(result.ok, result.summary)
        self.until(s, lambda s: getattr(s.state.pending_decision, 'kind', None) == 'combat.blockers')
        result = s.act('pilot:A', {'action_id': 'block', 'blocks': {}});self.assertTrue(result.ok, result.summary)
        self.priority(s, 'A')
        row = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:' + source.ref + ':'))
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:A', {'action_id': row['id'], 'targets': [resting.ref]}).ok);self.assertEqual(before, authoritative_state_hash(s.state))
        s = self.activate_counter_source(s, source, [attacker.ref], resume=True);self.settle(s)
        self.assertEqual('graveyard', s.state.cards[attacker.object_id].zone)
        self.assertEqual(0, s.state.cards[resting.object_id].marked_damage)
        self.step(s, 'A', 'beginning', 'upkeep');self.upkeep_choice(s, pay=False);self.settle(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.private(s);self.replay(s, load=True)
