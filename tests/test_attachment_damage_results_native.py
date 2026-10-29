"""Original attached damage results preserve quantities and affected seats."""
import unittest
import attachment_damage_fixture as fixtures
from quorune.record import authoritative_state_hash


class AttachmentDamageResultNativeTests(fixtures.AttachmentDamageFixture, unittest.TestCase):
    def test_actual_pollenbright_wings_uses_dealt_damage_for_distinct_saproling_tokens(self):
        s = self.session(120659300)
        aura = self.add(s.engine, 'Pollenbright Wings', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears')
        body.acquired_control_turn_count = -1
        self.assert_closed(s, aura)
        self.ready(s, {'C': 4, 'G': 1, 'W': 1})
        self.attach_cast(s, aura, body)
        self.assertIn('flying', s.engine._combat_keywords(s.state.cards[body.object_id]))
        self.attack(s, [(body, 'B')])
        self.damage_pending(s, aura)
        self.assertEqual(38, s.state.players['B'].life)
        s = self.replay(s, load=True)
        self.settle(s)
        tokens = [card for card in s.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(2, len(tokens))
        self.assertEqual(2, len({card.logical_object_id for card in tokens}))
        for card in tokens:
            self.assertEqual('A', card.controller)
            self.assertIn('saproling', str(s.engine._effective_card_data(card)['type_line']).casefold())
            self.assertEqual((1, 1), tuple(s.engine._numeric_stat(card.object_id, stat) for stat in ('power', 'toughness')))
            self.assertEqual(['G'], s.engine._effective_card_data(card)['colors'])
        self.private(s)
        self.replay(s, load=True)

    def test_actual_elder_mastery_noncombat_player_damage_discards_two_for_damaged_seat(self):
        s = self.session(120659310)
        aura = self.add(s.engine, 'Elder Mastery', zone='hand')
        body = self.add(s.engine, 'Prodigal Pyromancer', seat='B')
        body.acquired_control_turn_count = -1
        self.assert_closed(s, aura)
        self.ready(s, {'C': 3, 'U': 1, 'B': 1, 'R': 1})
        self.attach_cast(s, aura, body)
        hand = {seat: len(s.state.players[seat].zones['hand']) for seat in 'ABC'}
        self.ping(s, 'B', body, 'C')
        self.damage_pending(s, aura)
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        self.assertEqual(['C'], s.state.pending_decision.actors)
        refs = [s.state.cards[oid].ref for oid in s.state.players['C'].zones['hand'][:2]]
        for seat in 'ABD':
            self.assertIsNone(s.packet('pilot:' + seat, full=True)['decision'])
        s = self.replay(s, load=True)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C', {'action_id': 'choose', 'cards': refs[:1]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.choose_as(s, 'C', cards=refs)
        self.settle(s)
        self.assertEqual(hand['C'] - 2, len(s.state.players['C'].zones['hand']))
        self.assertEqual(hand['A'], len(s.state.players['A'].zones['hand']))
        self.assertEqual(hand['B'], len(s.state.players['B'].zones['hand']))
        self.assertTrue(all(next(card for card in s.state.cards.values() if card.ref == ref).zone == 'graveyard' for ref in refs))
        self.private(s)
        self.replay(s, load=True)

    def test_actual_destructive_urge_uses_damaged_player_land_choice(self):
        s = self.session(120659320)
        aura = self.add(s.engine, 'Destructive Urge', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears', seat='B')
        body.acquired_control_turn_count = -1
        land = self.add(s.engine, 'Island', seat='C')
        foreign = self.add(s.engine, 'Island', seat='B', ref='foreign-land-choice')
        self.assert_closed(s, aura)
        self.ready(s, {'C': 1, 'R': 2})
        self.attach_cast(s, aura, body)
        self.step(s, 'B', 'combat', 'declare_attackers')
        self.attack_for(s, 'B', [(body, 'C')])
        self.damage_pending(s, aura)
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        self.assertEqual(['C'], s.state.pending_decision.actors)
        s = self.replay(s, load=True)
        before = authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:C', {'action_id': 'choose', 'cards': [foreign.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        self.choose_as(s, 'C', cards=[land.ref])
        self.settle(s)
        self.assertEqual('graveyard', s.state.cards[land.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[foreign.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[aura.object_id].zone)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_lost_jitte_counters_from_blocker_combat_exclude_noncombat_and_pay_modal_cost(self):
        s = self.session(120659330)
        equipment = self.add(s.engine, 'Lost Jitte', zone='hand')
        body = self.add(s.engine, 'Prodigal Pyromancer')
        body.acquired_control_turn_count = -1
        attacker = self.add(s.engine, 'Grizzly Bears', seat='B')
        attacker.acquired_control_turn_count = -1
        self.assert_closed(s, equipment)
        self.ready(s, {'C': 2})
        self.cast_for(s, 'A', equipment, reserve={'C': 1})
        self.settle(s)
        self.activate(s, 'A', equipment, [body.ref])
        self.settle(s)
        self.assertEqual(body.object_id, s.state.cards[equipment.object_id].attached_to)
        self.ping(s, 'A', body, 'C')
        self.settle(s)
        self.assertEqual({}, s.state.cards[equipment.object_id].counters)
        self.step(s, 'B', 'combat', 'declare_attackers')
        # Prepared untapped blocker for this independent step; the action and
        # actual combat assignment below are authoritative native commands.
        s.state.cards[body.object_id].tapped = False
        self.checkpoint(s)
        self.attack_for(s, 'B', [(attacker, 'A')])
        self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.blockers')
        r = s.act('pilot:A', {'action_id': 'block', 'blocks': {body.ref: attacker.ref}})
        self.assertTrue(r.ok, r.summary)
        self.damage_pending(s, equipment)
        self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
        self.assertIsNone(s.state.cards[equipment.object_id].attached_to)
        self.settle(s)
        self.assertEqual(1, s.state.cards[equipment.object_id].counters['charge'])
        self.priority(s, 'A')
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'].startswith('activate:' + equipment.ref + ':'))
        before = authoritative_state_hash(s.state)
        command = {'action_id': offered['id'], 'modes': ['mode_2'], 'targets': [attacker.ref], 'pay': 'auto'}
        self.assertFalse(s.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(s.state))
        r = s.act('pilot:A', command)
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(0, s.state.cards[equipment.object_id].counters.get('charge', 0))
        self.settle(s)
        self.private(s)
        self.replay(s, load=True)
