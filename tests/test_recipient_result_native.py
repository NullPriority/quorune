"""Original-card recipient lifetime and binding-aware optional compositions."""
import unittest
import recipient_result_fixture as fixtures

class RecipientResultNativeTests(fixtures.RecipientResultFixture, unittest.TestCase):

    def test_actual_mire_blight_keeps_original_receiver_after_giver_departure_or_reentry(self):
        for reenter in (False, True):
            with self.subTest(reenter=reenter):
                s = self.session(613655001 + reenter)
                aura = self.add(s.engine, 'Mire Blight', zone='hand')
                victim = self.add(s.engine, 'Grizzly Bears')
                pyro = self.add(s.engine, 'Prodigal Pyromancer')
                bounce = self.add(s.engine, 'Unsummon', zone='hand')
                remove = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
                self.add(s.engine, 'Vedalken Orrery')
                self.add(s.engine, 'Divination', zone='hand', ref='private-recipient-hand')
                self.assert_closed(s, aura)
                self.prepare(s, mana={'B': 1, 'U': 1, 'G': 1, 'C': 1})
                s.state.players['B'].mana_pool.update({'C': 1, 'W': 1})
                self.checkpoint(s)
                self.cast(s, 'A', aura, [victim.ref])
                self.settle(s)
                self.assertEqual(victim.object_id, s.state.cards[aura.object_id].attached_to)
                self.activate(s, 'A', pyro, [victim.ref])
                self.resolve_top(s)
                self.assertEqual(1, s.state.cards[victim.object_id].marked_damage)
                self.assertEqual(1, self.trigger_count(s, aura))
                identity = s.state.cards[victim.object_id].logical_object_id
                if reenter:
                    self.cast(s, 'A', bounce, [victim.ref])
                    self.resolve_top(s)
                    self.cast(s, 'A', victim)
                    self.resolve_top(s)
                    self.assertNotEqual(identity, s.state.cards[victim.object_id].logical_object_id)
                else:
                    self.cast(s, 'B', remove, [aura.ref])
                    self.resolve_top(s)
                s = self.replay(s, load=True)
                self.settle(s)
                self.assertEqual('battlefield' if reenter else 'graveyard', s.state.cards[victim.object_id].zone)
                self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_frozen_solid_and_hot_soup_use_real_attachment_and_sealed_damage_result(self):
        for name in ('Frozen Solid', 'Hot Soup'):
            with self.subTest(name=name):
                s = self.session(613655010 + len(name))
                giver = self.add(s.engine, name, zone='hand')
                victim = self.add(s.engine, 'Grizzly Bears')
                pyro = self.add(s.engine, 'Prodigal Pyromancer')
                self.assert_closed(s, giver)
                self.prepare(s, mana={'C': 4, 'U': 2})
                self.checkpoint(s)
                self.cast(s, 'A', giver, [victim.ref] if name == 'Frozen Solid' else ())
                self.settle(s)
                if name == 'Hot Soup':
                    self.activate(s, 'A', giver, [victim.ref])
                    self.settle(s)
                self.assertEqual(victim.object_id, s.state.cards[giver.object_id].attached_to)
                self.activate(s, 'A', pyro, [victim.ref])
                self.resolve_top(s)
                self.assertEqual(1, self.trigger_count(s, giver))
                self.settle(s)
                self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
                self.replay(s, load=True)

    def test_actual_receiver_destruction_distinguishes_regeneration_prohibition_and_indestructible(self):
        for scene in ('ordinary', 'prohibited', 'indestructible'):
            with self.subTest(scene=scene):
                s = self.session(613655030 + len(scene))
                giver = self.add(s.engine, 'Mire Blight' if scene == 'ordinary' else 'Death Pits of Rath', zone='hand')
                victim = self.add(s.engine, 'Darksteel Myr' if scene == 'indestructible' else 'Horned Troll', seat='B')
                pyro = self.add(s.engine, 'Prodigal Pyromancer')
                self.assert_closed(s, giver)
                self.assert_closed(s, victim)
                self.prepare(s, mana={'C': 3, 'B': 2})
                s.state.players['B'].mana_pool['G'] = 1
                self.checkpoint(s)
                self.cast(s, 'A', giver, [victim.ref] if scene == 'ordinary' else ())
                self.settle(s)
                self.activate(s, 'A', pyro, [victim.ref])
                self.resolve_top(s)
                self.assertEqual(1, self.trigger_count(s, giver))
                if scene != 'indestructible':
                    self.activate(s, 'B', victim)
                    self.resolve_top(s)
                s = self.replay(s, load=True)
                self.settle(s)
                current = s.state.cards[victim.object_id]
                self.assertEqual('graveyard' if scene == 'prohibited' else 'battlefield', current.zone)
                if scene == 'ordinary':
                    self.assertTrue(current.tapped)
                    self.assertEqual(0, current.marked_damage)
                if scene == 'indestructible':
                    self.assertEqual(1, current.marked_damage)
                self.replay(s, load=True)

    def test_actual_cryoshatter_tap_cost_and_damage_are_separate_owned_subscription_children(self):
        for tap_cost in (False, True):
            with self.subTest(tap_cost=tap_cost):
                s = self.session(613655050 + tap_cost)
                aura = self.add(s.engine, 'Cryoshatter', zone='hand')
                victim = self.add(s.engine, 'Prodigal Pyromancer' if tap_cost else 'Grizzly Bears', ref='cryoshatter-victim')
                pyro = victim if tap_cost else self.add(s.engine, 'Prodigal Pyromancer', ref='cryoshatter-damager')
                self.assert_closed(s, aura)
                self.prepare(s, mana={'U': 1})
                self.checkpoint(s)
                self.cast(s, 'A', aura, [victim.ref])
                self.settle(s)
                self.activate(s, 'A', pyro, ['D'] if tap_cost else [victim.ref])
                if tap_cost:
                    self.assertEqual(1, self.trigger_count(s, aura))
                    self.assertEqual(aura.object_id, s.state.stack[-1].source_object_id)
                else:
                    self.resolve_top(s)
                    self.assertEqual(1, self.trigger_count(s, aura))
                self.resolve_top(s)
                self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
                self.settle(s)
                self.assertEqual(39 if tap_cost else 40, s.state.players['D'].life)
                self.replay(s, load=True)

    def test_actual_saber_and_broodhatch_choose_zero_or_all_actual_received_tokens_after_lethal(self):
        for name in ('Saber Ants', 'Broodhatch Nantuko'):
            for apply in (False, True):
                with self.subTest(name=name, apply=apply):
                    s = self.session(613655070 + len(name) + apply)
                    source = self.add(s.engine, name, zone='hand')
                    bolt = self.add(s.engine, 'Lightning Bolt', zone='hand')
                    self.add(s.engine, 'Divination', zone='hand', ref='private-recipient-hand')
                    self.assert_closed(s, source)
                    self.prepare(s, mana={'C': 3, 'G': 1, 'R': 1})
                    self.checkpoint(s)
                    self.cast_for(s, 'A', source, reserve={'R': 1})
                    self.settle(s)
                    self.cast(s, 'A', bolt, [source.ref])
                    self.resolve_top(s)
                    self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    self.assertFalse(self.insects(s))
                    s = self.optional(s, apply)
                    self.settle(s)
                    tokens = self.insects(s)
                    self.assertEqual(3 if apply else 0, len(tokens))
                    for token in tokens:
                        self.assertEqual((1, 1), (s.engine._numeric_stat(token.object_id, 'power'), s.engine._numeric_stat(token.object_id, 'toughness')))
                        self.assertEqual({'G'}, set(s.engine._effective_card_data(token).get('colors', ())))
                    self.private(s)
                    self.replay(s, load=True)

    def test_actual_broodhatch_morph_does_not_gain_a_receiving_trigger_retroactively(self):
        s = self.session(613655090)
        source = self.add(s.engine, 'Broodhatch Nantuko', zone='hand')
        pyro = self.add(s.engine, 'Prodigal Pyromancer')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 5, 'G': 1})
        self.checkpoint(s)
        action = next((a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'] == 'cast-morph:' + source.ref))
        r = s.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)
        self.settle(s)
        self.assertTrue(s.state.cards[source.object_id].face_down)
        self.activate(s, 'A', pyro, [source.ref])
        self.settle(s)
        self.assertFalse(self.insects(s))
        self.assertEqual(0, self.trigger_count(s, source))
        self.priority(s, 'A')
        action = next((a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'] == 'turn-face-up:' + source.ref))
        r = s.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(r.ok, r.summary)
        self.settle(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertFalse(self.insects(s))
        self.replay(s, load=True)

    def test_actual_sprouting_source_copy_uses_copiable_values_after_buff_or_lethal_and_copies_retrigger(self):
        for buff in (False, True):
            with self.subTest(buff=buff):
                s = self.session(613655100 + buff)
                source = self.add(s.engine, 'Sprouting Phytohydra', zone='hand')
                growth = self.add(s.engine, 'Giant Growth', zone='hand')
                shocks = [self.add(s.engine, 'Shock', zone='hand', ref='copy-shock-' + str(i)) for i in range(2)]
                self.add(s.engine, 'Divination', zone='hand', ref='private-recipient-hand')
                self.assert_closed(s, source)
                self.prepare(s, mana={'C': 4, 'G': 1 + int(buff), 'R': 2})
                self.checkpoint(s)
                self.cast_for(s, 'A', source, reserve={'G': int(buff), 'R': 2})
                self.settle(s)
                if buff:
                    self.cast_for(s, 'A', growth, [source.ref], reserve={'R': 2})
                    self.resolve_top(s)
                self.cast(s, 'A', shocks[0], [source.ref])
                self.resolve_top(s)
                self.assertEqual('battlefield' if buff else 'graveyard', s.state.cards[source.object_id].zone)
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, True)
                self.settle(s)
                copies = self.hydras(s)
                self.assertEqual(1, len(copies))
                token = copies[0]
                self.assertEqual((0, 2), (s.engine._numeric_stat(token.object_id, 'power'), s.engine._numeric_stat(token.object_id, 'toughness')))
                self.assertIn('defender', s.engine._combat_keywords(token))
                self.cast(s, 'A', shocks[1], [token.ref])
                self.resolve_top(s)
                self.assertNotIn(token.object_id, s.state.players['A'].zones['battlefield'])
                self.assertEqual('semantic.choice', self.resolve(s).kind)
                s = self.optional(s, True)
                self.settle(s)
                copies = self.hydras(s)
                self.assertEqual(1, len(copies))
                self.assertNotEqual(token.object_id, copies[0].object_id)
                self.private(s)
                self.replay(s, load=True)

    def test_actual_deep_slumber_self_untap_is_an_ordinary_effect_outside_its_step_restraint(self):
        s = self.session(613655120)
        source = self.add(s.engine, 'Deep-Slumber Titan', zone='hand')
        shock = self.add(s.engine, 'Shock', zone='hand')
        self.assert_closed(s, source)
        self.prepare(s, mana={'C': 2, 'R': 3})
        self.checkpoint(s)
        self.cast_for(s, 'A', source, reserve={'R': 1})
        self.settle(s)
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.cast(s, 'A', shock, [source.ref])
        self.settle(s)
        self.assertFalse(s.state.cards[source.object_id].tapped)
        self.assertEqual(2, s.state.cards[source.object_id].marked_damage)
        self.replay(s, load=True)
