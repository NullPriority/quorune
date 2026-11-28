from __future__ import annotations

import unittest


from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_colossal_grave_reaver_chooses_one_creature_from_the_real_mill_group(self):
        from quorune.record import authoritative_state_hash

        for has_choice in (False, True):
            with self.subTest(has_choice=has_choice):
                s = self.session(3640307 + int(has_choice))
                e = s.engine
                source = self.add(e,"Colossal Grave-Reaver",zone="hand")
                first = self.add(e,"Generic Bound Body",zone="library",ref="mill-creature-first")
                second = self.add(e,"Generic Bound Body" if has_choice else "Generic Bound Plains",
                                  zone="library",ref="mill-second-card")
                land = self.add(e,"Generic Bound Plains",zone="library",ref="mill-third-land")
                self.assert_whole_admission(source.printed_name)
                action = self.ready(s,source,{"C":6,"B":1,"G":1})
                self.checkpoint(s)
                cast = s.act("pilot:A", {"action_id":action["id"],"pay":"auto"})
                self.assertTrue(cast.ok, cast.summary)
                pending = self.resolve(s)
                if has_choice:
                    self.assertEqual(("semantic.choice",["A"]), (pending.kind,pending.actors))
                    options = s.packet("pilot:A",full=True)["decision"]["ctx"]["objects"]
                    self.assertEqual([first.ref,second.ref],sorted(row["id"] for row in options))
                    before = authoritative_state_hash(s.state)
                    self.assertFalse(s.act("pilot:B",{"action_id":"choose","card":first.ref}).ok)
                    self.assertFalse(s.act("pilot:A",{"action_id":"choose","card":land.ref}).ok)
                    self.assertEqual(before, authoritative_state_hash(s.state))
                    s = self.replay(s,load=True)
                    chosen = s.act("pilot:A",{"action_id":"choose","card":first.ref})
                    self.assertTrue(chosen.ok,chosen.summary)
                    self.assertIsNone(self.resolve(s))
                else:
                    self.assertIsNone(pending)
                self.assertEqual(["battlefield","graveyard","graveyard"],[
                    s.state.cards[card.object_id].zone for card in (first,second,land)
                ])
                self.replay(s,load=True)

    def test_colossal_grave_reaver_requires_the_complete_original_group_and_result(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.library_creature_arrival import CAPABILITY

        card = self.db.lookup("Colossal Grave-Reaver",fuzzy=False)
        arguments={"capability_registry":self.registry,"capability_profile":"commander_review"}
        ir=compile_oracle_card(card,**arguments)
        self.assertEqual("exact",ir.status,ir.material_residuals)
        self.assertTrue(any(CAPABILITY in node.capability_dependencies for face in ir.faces for node in face.nodes))
        for oracle in (card.oracle_text.replace("creature cards","cards"),
                       card.oracle_text.replace("put one of them","put them")):
            self.assertTrue(compile_oracle_card(replace(card,oracle_text=oracle),**arguments).material_residuals)
        with patch("quorune.rules.library_creature_arrival.arrival_node_capabilities",return_value=()):
            self.assertTrue(compile_oracle_card(card,**arguments).material_residuals)

    def test_original_anywhere_graveyard_land_mill_uses_destination_owner_and_replays(self):
        session = self.session(3640301)
        engine = session.engine
        observer = self.add(engine, 'Skola Grovedancer', zone='hand', ref='anywhere-graveyard-observer')
        foreign = self.add(engine, 'Skola Grovedancer', seat='B', ref='anywhere-graveyard-foreign-observer')
        self.assert_whole_admission(observer.printed_name)
        action = self.ready(session, observer, {'G': 4, 'C': 6})
        session.state.players['B'].mana_pool.update({'G': 1, 'C': 2})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'].startswith('activate:'+observer.ref+':'))
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.assertEqual(before, session.state.to_dict())
        life = {seat: session.state.players[seat].life for seat in 'AB'}
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        for _ in range(16):
            if session.state.players['A'].zones['graveyard']: break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertTrue(session.state.stack)
        self.assertEqual(life['A'], session.state.players['A'].life)
        self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(life['A']+1, session.state.players['A'].life)
        self.assertEqual(life['B'], session.state.players['B'].life)
        while session.state.priority_player != 'B':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                      if a['id'].startswith('activate:'+foreign.ref+':'))
        self.assertTrue(session.act('pilot:B', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual(life['A']+1, session.state.players['A'].life)
        self.assertEqual(life['B']+1, session.state.players['B'].life)
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)

    def test_original_anywhere_graveyard_land_discard_and_battlefield_origins_replay(self):
        session = self.session(3640302)
        engine = session.engine
        observer = self.add(engine, 'Skola Grovedancer', ref='anywhere-graveyard-hand-observer')
        spell = self.add(engine, 'Mind Rot', zone='hand', ref='anywhere-graveyard-discard-spell')
        destruction = self.add(engine, 'Stone Rain', zone='hand', ref='anywhere-graveyard-land-destruction')
        land = self.add(engine, 'Generic Bound Plains', ref='anywhere-graveyard-battlefield-land')
        discard_cards = [self.add(engine, 'Generic Bound Plains', zone='hand', ref=f'anywhere-graveyard-discard-{index}')
                         for index in range(2)]
        self.assert_whole_admission(observer.printed_name)
        action = self.ready(session, spell, {'B': 3, 'C': 6, 'R': 3})
        self.checkpoint(session)
        life = session.state.players['A'].life
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': ['A'], 'pay': 'auto'}).ok)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertTrue(session.act('pilot:A', {'action_id': 'choose', 'cards': [c.ref for c in discard_cards]}).ok)
        for _ in range(12):
            if session.state.pending_decision and session.state.pending_decision.kind == 'trigger.order':
                pending = [item for batch in session.state.pending_trigger_batches for item in batch.items]
                self.assertEqual(2, len(pending))
                self.replay(session, load=True)
                self.assertTrue(session.act('pilot:A', {'action_id': 'order', 'triggers': [item.ref for item in pending]}).ok)
            self.resolve(session)
            if not session.state.stack and not session.state.pending_trigger_batches:break
        self.assertEqual(life+2, session.state.players['A'].life)
        for card in discard_cards:self.assertEqual('graveyard', session.state.cards[card.object_id].zone)
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == destruction.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': [land.ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[land.object_id].zone)
        self.assertEqual(life+3, session.state.players['A'].life)
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)

    def test_original_anywhere_graveyard_simultaneous_death_checks_surviving_observers(self):
        session = self.session(3640303)
        engine = session.engine
        leaving = self.add(engine, 'Skola Grovedancer', ref='anywhere-graveyard-departing-observer')
        land_creature = self.add(engine, 'Dryad Arbor', ref='anywhere-graveyard-land-creature')
        surviving = self.add(engine, 'Profane Memento', seat='B', ref='anywhere-graveyard-surviving-observer')
        spell = self.add(engine, 'Wrath of God', zone='hand', ref='anywhere-graveyard-simultaneous-destruction')
        self.assert_whole_admission(leaving.printed_name)
        self.assert_whole_admission(surviving.printed_name)
        life = {seat: session.state.players[seat].life for seat in 'AB'}
        action = self.ready(session, spell, {'W': 4, 'C': 2})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        for _ in range(16):
            if session.state.pending_decision and session.state.pending_decision.kind == 'trigger.order':break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('graveyard', session.state.cards[leaving.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[land_creature.object_id].zone)
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        pending = [item for batch in session.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2, len(pending))
        self.assertEqual({surviving.object_id}, {item.source_object_id for item in pending})
        self.assertEqual({'B'}, {item.controller for item in pending})
        self.replay(session, load=True)
        self.assertTrue(session.act('pilot:B', {'action_id': 'order', 'triggers': [item.ref for item in pending]}).ok)
        self.resolve(session)
        self.assertEqual(life['A'], session.state.players['A'].life)
        self.assertEqual(life['B']+2, session.state.players['B'].life)
        self.replay(session, load=True)

    def test_original_anywhere_graveyard_color_optional_draw_discard_consequence_replays(self):
        session = self.session(3640304)
        engine = session.engine
        observer = self.add(engine, 'Compost', zone='hand', ref='anywhere-graveyard-color-observer')
        discard = self.add(engine, 'Mind Rot', zone='hand', ref='anywhere-graveyard-opponent-discard')
        black = self.add(engine, 'Murder', seat='B', zone='hand', ref='anywhere-graveyard-black-card')
        land = self.add(engine, 'Generic Bound Plains', seat='B', zone='hand', ref='anywhere-graveyard-colorless-card')
        self.assert_whole_admission(observer.printed_name)
        action = self.ready(session, observer, {'G': 3, 'B': 3, 'C': 5})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player != 'A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                      if a.get('card') == discard.ref)
        self.assertTrue(session.act('pilot:A', {'action_id': action['id'], 'targets': ['B'], 'pay': 'auto'}).ok)
        self.assertIsNotNone(self.resolve(session))
        self.assertTrue(session.act('pilot:B', {'action_id': 'choose', 'cards': [black.ref, land.ref]}).ok)
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        hand = len(session.state.players['A'].zones['hand'])
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id': 'choose', 'choice': 'apply'}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.replay(session, load=True)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'choice': 'apply'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(hand+1, len(session.state.players['A'].zones['hand']))
        self.assertEqual('graveyard', session.state.cards[black.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[land.object_id].zone)
        for seat in 'BCD':
            self.assertNotIn('hand', session.packet('pilot:'+seat, full=True)['state']['players']['A'])
        self.replay(session, load=True)
