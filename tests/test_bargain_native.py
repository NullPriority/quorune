"""Original Bargain casts retain source-linked choices and ordinary results."""
import unittest
import bargain_fixture as fixtures
from quorune.record import authoritative_state_hash


class BargainNativeTests(fixtures.BargainFixture, unittest.TestCase):
    def test_actual_redirected_bargain_price_resumes_with_same_declaration_and_private_cast_frame(self):
        s = self.session(702166020)
        source = self.add(s.engine, 'High Fae Negotiator', zone='hand')
        price = self.add(s.engine, 'Ornithopter')
        for index in range(2):self.add(s.engine, 'Dauthi Voidwalker', seat='B', ref='bargain-redirect-' + str(index))
        self.assert_closed(s, source);self.ready(s, {'C': 3, 'B': 2})
        self.cast_bargain(s, source, price)
        self.assertEqual('replacement.order', s.state.pending_decision.kind)
        self.assertEqual('battlefield', s.state.cards[price.object_id].zone)
        self.assertNotIn('private-recipient-hand', str(s.packet('pilot:C', full=True)))
        s = self.replay(s, load=True)
        while s.state.pending_decision.kind == 'replacement.order':
            principal = s.pending_principals()[0]
            option = s.packet(principal, full=True)['decision']['ctx']['options'][0]['id']
            before = authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:D', {'action_id': 'choose', 'replacement': option}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
            accepted = s.act(principal, {'action_id': 'choose', 'replacement': option})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('exile', s.state.cards[price.object_id].zone)
        self.settle(s)
        self.assertEqual([43, 37, 37, 37], [s.state.players[p].life for p in 'ABCD'])
        self.private(s);self.replay(s, load=True)

    def test_actual_high_fae_paid_and_unpaid_casts_keep_opponent_life_results_linked(self):
        for chosen in (True, False):
            with self.subTest(chosen=chosen):
                s = self.session(702166001 + chosen)
                source = self.add(s.engine, 'High Fae Negotiator', zone='hand')
                price = self.add(s.engine, 'Sol Ring')
                self.assert_closed(s, source);self.ready(s, {'C': 3, 'B': 2})
                self.cast_bargain(s, source, price, chosen=chosen);self.settle(s)
                self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
                self.assertEqual('graveyard' if chosen else 'battlefield', s.state.cards[price.object_id].zone)
                self.assertEqual([43, 37, 37, 37] if chosen else [40] * 4, [s.state.players[p].life for p in 'ABCD'])
                self.private(s);self.replay(s, load=True)

    def test_actual_bargain_offers_enchantment_or_artifact_and_rejects_foreign_or_ordinary_creature(self):
        s = self.session(702166010)
        source = self.add(s.engine, 'High Fae Negotiator', zone='hand')
        enchantment = self.add(s.engine, 'Fervor')
        artifact = self.add(s.engine, 'Sol Ring')
        ordinary = self.add(s.engine, 'Grizzly Bears')
        foreign = self.add(s.engine, 'Sol Ring', seat='B', ref='foreign-bargain-price')
        self.assert_closed(s, source);self.ready(s, {'C': 3, 'B': 2})
        action, option = self.bargain_action(s, source)
        refs = set(option['choice_schema']['sacrifice_cards']['legal_refs'])
        self.assertIn(enchantment.ref, refs);self.assertIn(artifact.ref, refs)
        self.assertNotIn(ordinary.ref, refs);self.assertNotIn(foreign.ref, refs)
        before = authoritative_state_hash(s.state)
        for ref in (ordinary.ref, foreign.ref):
            self.assertFalse(s.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'], 'sacrifice_cards': [ref], 'pay': 'auto'}).ok)
            self.assertEqual(before, authoritative_state_hash(s.state))
        self.cast_bargain(s, source, enchantment);self.settle(s)
        self.assertEqual('graveyard', s.state.cards[enchantment.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[artifact.object_id].zone)
        self.assertEqual(43, s.state.players['A'].life)
        self.private(s);self.replay(s, load=True)
