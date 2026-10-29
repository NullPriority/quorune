"""Original Renown source and global creature observers share public events."""
import json
import unittest
import designation_event_fixture as fixtures
from quorune.record import authoritative_state_hash


class RenownedEventNativeTests(fixtures.DesignationEventFixture, unittest.TestCase):
    def test_actual_relic_and_valeron_public_designation_draw_and_private_equipment_search_compose(self):
        s = self.session(613657201)
        observer = self.add(s.engine, 'Valeron Wardens', zone='hand')
        source = self.add(s.engine, 'Relic Seeker', zone='hand')
        equipment = self.add(s.engine, 'Hot Soup', zone='library')
        non_equipment = self.add(s.engine, 'Sol Ring', zone='library')
        self.add(s.engine, 'Fervor')
        self.assert_closed(s, observer)
        self.assert_closed(s, source)
        self.ready(s, {'C': 3, 'G': 1, 'W': 1})
        self.cast_for(s, 'A', observer, reserve={'C': 1, 'W': 1})
        self.settle(s)
        self.cast(s, 'A', source)
        self.settle(s)
        hand = len(s.state.players['A'].zones['hand'])
        self.attack(s, [(source, 'B')])
        self.until(s, lambda s: self.trigger_count(s, source) == 1)
        self.resolve_top(s)
        self.assertTrue(s.state.cards[source.object_id].renowned)
        self.assertEqual(1, s.state.cards[source.object_id].counters.get('+1/+1'))
        self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'priority')
        self.assertEqual(1, self.trigger_count(s, observer))
        self.assertEqual(1, self.trigger_count(s, source))
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.optional(s, True)
        self.assertEqual('semantic.search', self.resolve(s).kind)
        before = authoritative_state_hash(s.state)
        for principal, refs in (('pilot:C', [equipment.ref]), ('pilot:A', [non_equipment.ref])):
            self.assertFalse(s.act(principal, {'action_id': 'choose', 'search_cards': refs}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        for seat in 'BCD':
            packet = json.dumps(s.packet('pilot:' + seat, full=True))
            self.assertNotIn(non_equipment.ref, packet)
        s = self.replay(s, load=True)
        before_shuffle = s.state.players['A'].stats.get('shuffle_count', 0)
        r = s.act('pilot:A', {'action_id': 'choose', 'search_cards': [equipment.ref]})
        self.assertTrue(r.ok, r.summary)
        self.settle(s)
        self.assertEqual('hand', s.state.cards[equipment.object_id].zone)
        self.assertEqual(hand + 2, len(s.state.players['A'].zones['hand']))
        self.assertEqual(before_shuffle + 1, s.state.players['A'].stats.get('shuffle_count', 0))
        self.private(s)
        self.replay(s, load=True)

    def test_actual_valeron_observes_its_own_renown_but_not_foreign_renown(self):
        for own in (False, True):
            with self.subTest(own=own):
                s = self.session(613657220 + own)
                observer = self.add(s.engine, 'Valeron Wardens', zone='hand')
                foreign = self.add(s.engine, 'Topan Freeblade', seat='B')
                self.add(s.engine, 'Fervor')
                self.assert_closed(s, observer)
                self.ready(s, {'C': 2, 'G': 1})
                self.cast(s, 'A', observer)
                self.settle(s)
                hand = len(s.state.players['A'].zones['hand'])
                if own:
                    self.attack(s, [(observer, 'C')])
                else:
                    self.step(s, 'B', 'precombat_main', 'main')
                    self.until(s, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == 'combat.attackers')
                    r = s.act('pilot:B', {'action_id': 'attack', 'attackers': {foreign.ref: 'C'}})
                    self.assertTrue(r.ok, r.summary)
                subject = observer if own else foreign
                self.until(s, lambda s: s.state.cards[subject.object_id].renowned)
                self.assertEqual(1 if own else 0, self.trigger_count(s, observer))
                self.settle(s)
                self.assertEqual(hand + own, len(s.state.players['A'].zones['hand']))
                self.private(s)
                self.replay(s, load=True)
