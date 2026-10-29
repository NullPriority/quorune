"""Actual Aura spells preserve their controller's separate life triggers."""
import unittest
import attachment_damage_fixture as fixtures


class AttachmentDamageLifeNativeTests(fixtures.AttachmentDamageFixture, unittest.TestCase):
    def test_actual_spirit_vampiric_and_armadillo_links_gain_for_aura_controller_after_foreign_damage(self):
        for index, (name, mana, damage) in enumerate((('Spirit Link', {'W': 1}, 2),
                                                     ('Vampiric Link', {'B': 1}, 2),
                                                     ('Armadillo Cloak', {'C': 1, 'G': 1, 'W': 1}, 4))):
            with self.subTest(card=name):
                s = self.session(120659100 + index)
                aura = self.add(s.engine, name, zone='hand')
                body = self.add(s.engine, 'Grizzly Bears', seat='B')
                body.acquired_control_turn_count = -1
                self.assert_closed(s, aura)
                self.ready(s, mana)
                self.attach_cast(s, aura, body)
                self.step(s, 'B', 'combat', 'declare_attackers')
                before = {seat: s.state.players[seat].life for seat in 'AB'}
                self.attack_for(s, 'B', [(body, 'A')])
                self.damage_pending(s, aura)
                self.assertEqual(before['A'] - damage, s.state.players['A'].life)
                self.assertEqual(before['B'], s.state.players['B'].life)
                self.assertEqual('A', next(item.controller for item in s.state.stack if item.source_object_id == aura.object_id))
                s = self.replay(s, load=True)
                self.settle(s)
                self.assertEqual(before['A'], s.state.players['A'].life)
                self.assertEqual(before['B'], s.state.players['B'].life)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_spirit_loop_noncombat_gain_survives_provider_destruction_and_returns_aura(self):
        s = self.session(120659110)
        aura = self.add(s.engine, 'Spirit Loop', zone='hand')
        body = self.add(s.engine, 'Prodigal Pyromancer')
        body.acquired_control_turn_count = -1
        destroy = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
        self.add(s.engine, 'Plains', seat='B')
        self.add(s.engine, 'Island', seat='B')
        self.assert_closed(s, aura)
        self.ready(s, {'C': 1, 'W': 1})
        self.attach_cast(s, aura, body)
        before = s.state.players['A'].life
        self.ping(s, 'A', body, 'C')
        self.until(s, lambda s: self.trigger_count(s, aura) == 1)
        self.assertEqual(before, s.state.players['A'].life)
        self.cast(s, 'B', destroy, [aura.ref])
        s = self.replay(s, load=True)
        self.settle(s)
        self.assertEqual('hand', s.state.cards[aura.object_id].zone)
        self.assertEqual(before + 1, s.state.players['A'].life)
        self.private(s)
        self.replay(s, load=True)

    def test_actual_soul_link_self_damage_creates_distinct_dealt_and_received_occurrences(self):
        s = self.session(120659120)
        aura = self.add(s.engine, 'Soul Link', zone='hand')
        body = self.add(s.engine, 'Prodigal Pyromancer', seat='B')
        body.acquired_control_turn_count = -1
        self.assert_closed(s, aura)
        self.ready(s, {'C': 1, 'W': 1, 'B': 1})
        self.attach_cast(s, aura, body)
        before = {seat: s.state.players[seat].life for seat in 'AB'}
        self.ping(s, 'B', body, body.ref)
        self.until(s, lambda s: self.trigger_count(s, aura) == 2)
        self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        s = self.replay(s, load=True)
        self.settle(s)
        self.assertEqual(before['A'] + 2, s.state.players['A'].life)
        self.assertEqual(before['B'], s.state.players['B'].life)
        self.private(s)
        self.replay(s, load=True)
