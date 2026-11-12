"""Actual entry, land, activated, optional and spell ordering compositions."""
import json
import unittest
from dataclasses import replace
import top_order_fixture as fixtures
from quorune.record import authoritative_state_hash


class TopOrderNativeTests(fixtures.TopOrderFixture,unittest.TestCase):
    def test_actual_sage_owl_order_survives_source_bounce_and_is_not_scry(self):
        session=self.session(613652001);source=self.add(session.engine,'Sage Owl',zone='hand');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.top_cards(session);self.assert_closed(session,source);self.prepare(session,mana={'C':1,'U':1});self.checkpoint(session);original=list(session.state.players['A'].zones['library']);self.cast(session,'A',source);self.resolve_top(session);self.cast(session,'B',bounce,[source.ref]);self.resolve_top(session);self.assertEqual('hand',session.state.cards[source.object_id].zone);expected=self.top_refs(session,4);self.assertEqual('semantic.choice',self.resolve(session).kind)
        session,chosen=self.choose_order(session,expected);self.settle(session);self.assertEqual(chosen,self.top_refs(session,4));self.assertEqual(original[:-4],session.state.players['A'].zones['library'][:-4]);self.assertFalse(any(e.code=='library.scry' for e in session.state.events));self.replay(session,load=True)

    def test_actual_halimar_enters_tapped_orders_three_and_retains_intrinsic_blue_mana(self):
        session=self.session(613652010);source=self.add(session.engine,'Halimar Depths',zone='hand');untap=self.add(session.engine,'Twiddle',zone='hand');self.add(session.engine,'Island');self.top_cards(session);self.assert_closed(session,source);self.prepare(session);self.checkpoint(session);result=session.act('pilot:A',{'action_id':'play-land:'+source.ref});self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[source.object_id].tapped);self.assertEqual(1,self.trigger_count(session,source));expected=self.top_refs(session,3);self.assertEqual('semantic.choice',self.resolve(session).kind);session,chosen=self.choose_order(session,expected);self.settle(session)
        self.cast(session,'A',untap,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind);result=session.act('pilot:A',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary);self.settle(session);self.priority(session,'A');action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertFalse(session.state.stack);self.assertEqual(1,session.state.players['A'].mana_pool['U']);self.assertEqual(chosen,self.top_refs(session,3));self.replay(session,load=True)

    def test_actual_aven_entry_and_twice_paid_activation_are_separate_counterable_orders(self):
        session=self.session(613652020);source=self.add(session.engine,'Aven Fateshaper',zone='hand');counter=self.add(session.engine,'Stifle',seat='B',zone='hand');self.add(session.engine,'Island',seat='B');self.top_cards(session);self.assert_closed(session,source);self.prepare(session,mana={'C':13,'U':4});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'C':8,'U':2});self.resolve_top(session);expected=self.top_refs(session,4);self.assertEqual('semantic.choice',self.resolve(session).kind);session,_=self.choose_order(session,expected);self.settle(session)
        for _ in range(2):
            self.priority(session,'A');action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual('priority',session.state.pending_decision.kind)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));target=session.state.stack[-1].ref;self.cast(session,'B',counter,[target]);self.resolve_top(session);self.assertEqual(1,len(session.state.stack));expected=self.top_refs(session,4);self.assertEqual('semantic.choice',self.resolve(session).kind);session,_=self.choose_order(session,expected);self.settle(session);self.replay(session,load=True)

    def test_actual_seer_tap_cost_short_library_and_index_empty_library(self):
        for empty in (False,True):
            with self.subTest(empty=empty):
                session=self.session(613652030+empty);source=self.add(session.engine,'Index' if empty else 'Gilt-Leaf Seer',zone='hand');cards=self.top_cards(session);keep=set() if empty else {cards[0].object_id}
                for oid in tuple(session.state.players['A'].zones['library']):
                    if oid not in keep:session.engine.move_card(oid,'outside',reason='Prepared short library boundary',semantic_events=False)
                if not empty:self.add(session.engine,'Fervor')
                self.assert_closed(session,source);self.prepare(session,mana={'C':2,'G':2,'U':1});self.checkpoint(session);self.cast_for(session,'A',source,reserve={} if empty else {'G':1});self.settle(session)
                if not empty:
                    self.priority(session,'A');action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'));result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[source.object_id].tapped);self.assertEqual('semantic.choice',self.resolve(session).kind);session,_=self.choose_order(session,[cards[0].ref]);self.settle(session)
                self.assertEqual(0 if empty else 1,len(session.state.players['A'].zones['library']));self.assertFalse(session.state.players['A'].attempted_empty_draw);self.replay(session,load=True)

    def test_actual_mirri_optional_upkeep_decline_does_not_look_and_apply_orders_three(self):
        from quorune.model import TurnEntry
        for apply in (False,True):
            with self.subTest(apply=apply):
                session=self.session(613652040+apply);source=self.add(session.engine,"Mirri's Guile",zone='hand');self.top_cards(session);self.assert_closed(session,source);self.prepare(session,mana={'G':1});self.checkpoint(session);self.cast(session,'A',source);self.settle(session);self.replay(session,load=True);e=session.engine;e.permissions.invalidate_current();session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[];e._begin_turn(TurnEntry(turn_id='prepared-order-upkeep',player='A',created_sequence=session.state.turn_sequence));e.pump();self.checkpoint(session);self.assertEqual('semantic.choice',self.resolve(session).kind);before=sum(x.code=='library.look' for x in session.state.events);expected=self.top_refs(session,3);result=session.act('pilot:A',{'action_id':'choose','choice':'apply' if apply else 'decline'});self.assertTrue(result.ok,result.summary)
                if apply:session,_=self.choose_order(session,expected)
                self.settle(session);self.assertEqual(before+int(apply),sum(x.code=='library.look' for x in session.state.events));self.replay(session,load=True)

    def test_actual_ponder_omen_and_entry_order_then_optional_shuffle_then_one_private_draw(self):
        for name,shuffle in (('Ponder',False),('Omen',True),('Pondering Mage',False)):
            with self.subTest(name=name):
                session=self.session(613652050+len(name));source=self.add(session.engine,name,zone='hand');self.top_cards(session);self.assert_closed(session,source);self.prepare(session,mana={'C':8,'U':4});self.checkpoint(session);self.cast(session,'A',source);hand=len(session.state.players['A'].zones['hand']);expected=self.top_refs(session,3);self.assertEqual('semantic.choice',self.resolve(session).kind);self.assertEqual(hand,len(session.state.players['A'].zones['hand']));session,chosen=self.choose_order(session,expected);self.assertEqual('semantic.choice',session.state.pending_decision.kind);self.assertEqual(hand,len(session.state.players['A'].zones['hand']));session=self.replay(session,load=True);before=sum(e.code=='library.shuffle' for e in session.state.events);result=session.act('pilot:A',{'action_id':'choose','choice':'apply' if shuffle else 'decline'});self.assertTrue(result.ok,result.summary);self.settle(session);self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']));self.assertEqual(before+int(shuffle),sum(e.code=='library.shuffle' for e in session.state.events))
                if not shuffle:self.assertIn(chosen[0],[session.state.cards[oid].ref for oid in session.state.players['A'].zones['hand']])
                self.replay(session,load=True)

    def test_actual_discombobulate_lost_counter_target_prevents_the_order(self):
        for lost in (False,True):
            with self.subTest(lost=lost):
                session=self.session(613652070+lost);source=self.add(session.engine,'Discombobulate',zone='hand');spell=self.add(session.engine,'Shock',seat='B',zone='hand');counter=self.add(session.engine,'Counterspell',seat='B',zone='hand');self.add(session.engine,'Island',seat='B',ref='counter-island-1');self.add(session.engine,'Island',seat='B',ref='counter-island-2');self.top_cards(session);self.assert_closed(session,source);self.prepare(session,mana={'C':3,'U':2});session.state.players['B'].mana_pool['R']=1;self.checkpoint(session);before=list(session.state.players['A'].zones['library']);self.cast(session,'B',spell,['C']);ref=next(i.ref for i in session.state.stack if i.card_object_id==spell.object_id);self.cast(session,'A',source,[ref])
                if lost:self.cast(session,'B',counter,[ref]);self.resolve_top(session);self.settle(session);self.assertEqual(before,session.state.players['A'].zones['library'])
                else:
                    expected=self.top_refs(session,4);self.assertEqual('semantic.choice',self.resolve(session).kind);session,_=self.choose_order(session,expected);self.settle(session)
                self.assertEqual(40,session.state.players['C'].life);self.replay(session,load=True)

    def test_runtime_ordering_owner_is_inferred_without_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.library_order_model import TOP_ORDER_CAPABILITY,SHUFFLE_CAPABILITY
        session=self.session(613652080);checked=0
        for name in ('Index','Ponder','Sage Owl'):
            source=self.add(session.engine,name)
            for p in session.engine.semantics.programs_for_oracle(source.oracle_id):
                for cap in (TOP_ORDER_CAPABILITY,SHUFFLE_CAPABILITY):
                    if cap not in p.capability_dependencies:continue
                    deps=tuple(c for c in p.capability_dependencies if c!=cap);q=replace(p,coverage=[],capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None);b=bind_semantic_program_runtime(q,capability_registry=self.registry,profile='commander_review');self.assertFalse(b['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,b['blockers']);checked+=1
        self.assertEqual(4,checked)


    def _choose_hand_top(self, session, refs):
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        self.assertEqual(("pilot:A",), tuple(session.pending_principals()))
        decision = session.packet("pilot:A", full=True)["decision"]
        schema = decision["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual("top_to_bottom", schema["order"])
        self.assertEqual(len(refs), schema.get("minimum", 1))
        self.assertEqual(len(refs), schema.get("maximum", 1))
        self.assertLessEqual(set(refs), set(schema["legal_refs"]))
        form = decision["legal_actions"][0]["form"]
        field = next(value for value in form["fields"] if value["name"] == "cards")
        self.assertTrue(field["ordered"])
        self.assertEqual("top_to_bottom", field["order"])
        before = authoritative_state_hash(session.state)
        invalid = [("pilot:B", refs), ("pilot:A", []),
                   ("pilot:A", [*refs[:-1], "foreign-hand"])]
        if len(refs) > 1:
            invalid.append(("pilot:A", [refs[0]] * len(refs)))
        for principal, cards in invalid:
            rejected = session.act(principal, {"action_id": "choose", "cards": cards})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        accepted = session.act("pilot:A", {"action_id": "choose", "cards": refs})
        self.assertTrue(accepted.ok, accepted.summary)
        return session

    def test_actual_brainstorm_chooses_a_private_post_draw_subset_and_replays_order(self):
        session = self.session(401_830_001)
        source = self.add(session.engine, "Brainstorm", zone="hand")
        old = self.add(session.engine, "Memnite", zone="hand", ref="brainstorm-old-hand")
        drawn = self.top_cards(session)[:3]
        self.assert_closed(session, source)
        self.prepare(session, mana={"U": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in drawn))
        refs = [drawn[-1].ref, old.ref]
        for seat in "BCD":
            packet = session.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            for ref in refs:
                self.assertNotIn(json.dumps(ref), json.dumps(packet))
        session = self._choose_hand_top(session, refs)
        self.settle(session)
        self.assertEqual(refs, self.top_refs(session, 2))
        self.assertEqual(session.state.cards[old.object_id].zone_timestamp,
                         session.state.cards[drawn[-1].object_id].zone_timestamp)
        self.assertEqual("hand", session.state.cards[drawn[0].object_id].zone)
        for seat in "BCD":
            packet = json.dumps(session.packet("pilot:" + seat, full=True))
            for ref in refs:
                self.assertNotIn(json.dumps(ref), packet)
        self.replay(session, load=True)

    def test_actual_brainstone_pays_before_draw_and_resumes_a_private_commander_replacement(self):
        session = self.session(401_830_002)
        engine = session.engine
        source = self.add(engine, "Brainstone", zone="hand")
        player = session.state.players["A"]
        commander_id = player.zones["command"].pop()
        commander = session.state.cards[commander_id]
        commander.zone = "hand"
        commander.known_to = ["A"]
        commander.revealed_to = []
        player.zones["hand"].append(commander_id)
        chosen = self.top_cards(session)[0]
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        self.cast_for(session, "A", source, reserve={"C": 2})
        self.settle(session)
        self.priority(session, "A")
        offer = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"].startswith(f"activate:{source.ref}:"))
        result = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        session = self._choose_hand_top(session, [commander.ref, chosen.ref])
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual(("pilot:A",), tuple(session.pending_principals()))
        for card in (commander, chosen):
            self.assertEqual("hand", session.state.cards[card.object_id].zone)
        for seat in "BCD":
            packet = session.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(json.dumps(chosen.ref), json.dumps(packet))
        session = self.replay(session, load=True)
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        option = next(row for row in ctx["options"] if not row.get("decline"))
        result = session.act("pilot:A", {"action_id": "choose", "replacement": option["id"]})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual("command", session.state.cards[commander_id].zone)
        self.assertEqual([chosen.ref], self.top_refs(session, 1))
        self.replay(session, load=True)

    def test_actual_blocked_draw_still_puts_back_the_only_hand_card(self):
        session = self.session(401_830_003)
        engine = session.engine
        player = session.state.players["A"]
        for object_id in tuple(player.zones["hand"]):
            engine.move_card(object_id, "graveyard", log=False)
        source = self.add(engine, "Brainstorm", zone="hand")
        previous = self.add(engine, "Divination", zone="hand")
        restriction = self.add(engine, "Spirit of the Labyrinth", seat="B")
        drawn = self.top_cards(session)[0]
        for card in (source, previous, restriction):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 2, "U": 2})
        self.checkpoint(session)
        self.cast_for(session, "A", previous, reserve={"U": 1})
        self.settle(session)
        self.assertEqual({source.object_id, drawn.object_id}, set(player.zones["hand"]))
        before = len(player.zones["library"])
        self.cast_for(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(before, len(player.zones["library"]))
        session = self._choose_hand_top(session, [drawn.ref])
        self.settle(session)
        self.assertEqual([], session.state.players["A"].zones["hand"])
        self.assertEqual([drawn.ref], self.top_refs(session, 1))
        self.replay(session, load=True)

    def test_actual_riverwise_augur_resolves_draw_before_private_top_order(self):
        session = self.session(401_830_004)
        source = self.add(session.engine, "Riverwise Augur", zone="hand")
        drawn = self.top_cards(session)[:3]
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 3, "U": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in drawn))
        refs = [drawn[1].ref, drawn[0].ref]
        session = self._choose_hand_top(session, refs)
        self.settle(session)
        self.assertEqual(refs, self.top_refs(session, 2))
        self.replay(session)


    def test_actual_brainstorm_keeps_put_back_after_a_resumed_dredge(self):
        session = self.session(401_830_005)
        source = self.add(session.engine, "Brainstorm", zone="hand")
        dredger = self.add(session.engine, "Dakmor Salvage", zone="graveyard", ref="brainstorm-dredger")
        top = self.top_cards(session)
        self.assert_closed(session, source)
        self.assert_closed(session, dredger)
        self.prepare(session, mana={"U": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("draw.replacement", self.resolve(session).kind)
        self.assertEqual([card.ref for card in top], self.top_refs(session, 5))
        session = self.replay(session, load=True)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        dredge = next(row for row in options if "Dredge" in row["label"])
        accepted = session.act("pilot:A", {"action_id": "choose", "choice": dredge["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "graveyard" for card in top[:2]))
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in top[2:4]))
        self.assertEqual("hand", session.state.cards[dredger.object_id].zone)
        refs = [top[3].ref, dredger.ref]
        session = self._choose_hand_top(session, refs)
        self.settle(session)
        self.assertEqual(refs, self.top_refs(session, 2))
        self.replay(session, load=True)


    def test_opponent_second_draw_composes_prep_counter_draw_and_graveyard_return(self):
        from quorune.preparation_model import PREPARED_FIELD, PreparedDesignation

        session = self.session(121_832_001)
        engine = session.engine
        mastermind = self.add(engine, "Faerie Mastermind", zone="hand")
        dusk = self.add(engine, "Defacing Duskmage")
        widow = self.add(engine, "Black Widow, Agile Avenger")
        detective = self.add(engine, "Dogged Detective", zone="graveyard")
        first = self.add(engine, "Reach Through Mists", seat="B", zone="hand")
        brainstorm = self.add(engine, "Brainstorm", seat="B", zone="hand")
        for index in range(2):
            self.add(engine, "Island", seat="B", ref=f"ordinal-island-{index}")
        for source in (mastermind, dusk, widow, detective, first, brainstorm):
            self.assert_closed(session, source)
        programs = [p for p in engine.semantics.programs_for_oracle(mastermind.oracle_id)
                    if p.event == "card.second_draw"]
        self.assertEqual(1, len(programs))
        self.assertTrue(programs[0].provenance["authored_by"].startswith("oracle-ir-v"))
        self.prepare(session, mana={"C": 1, "U": 1})
        self.checkpoint(session)

        self.cast_for(session, "B", first)
        self.assertIsNone(self.resolve(session))
        turn = str(session.state.turn_sequence)
        b_player = session.state.players["B"]
        self.assertEqual(1, b_player.stats["cards_drawn_by_turn"][turn])
        self.cast_for(session, "A", mastermind)
        self.assertIsNone(self.resolve(session))
        a_draws = session.state.players["A"].stats.get("cards_drawn_by_turn", {}).get(turn, 0)

        before = set(b_player.zones["hand"])
        self.cast_for(session, "B", brainstorm)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(("pilot:B",), tuple(session.pending_principals()))
        self.assertEqual(4, b_player.stats["cards_drawn_by_turn"][turn])
        self.assertFalse(any(item.source_object_id in {source.object_id for source in (mastermind, dusk, widow, detective)}
                             for item in session.state.stack))
        drawn = [session.state.cards[oid].ref for oid in b_player.zones["hand"] if oid not in before]
        self.assertEqual(3, len(drawn))
        foreign = session.packet("pilot:A", full=True)
        self.assertIsNone(foreign["decision"])
        for ref in drawn:
            self.assertNotIn(json.dumps(ref), json.dumps(foreign))
        session = self.replay(session, load=True)
        accepted = session.act("pilot:B", {"action_id": "choose", "cards": [drawn[2], drawn[0]]})
        self.assertTrue(accepted.ok, accepted.summary)

        self.assertEqual("trigger.order", session.state.pending_decision.kind)
        self.assertEqual(("pilot:A",), tuple(session.pending_principals()))
        triggers = [item for batch in session.state.pending_trigger_batches for item in batch.items]
        self.assertCountEqual([card.object_id for card in (mastermind, dusk, widow, detective)],
                              [item.source_object_id for item in triggers])
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        ordered = session.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in ctx["triggers"]]})
        self.assertTrue(ordered.ok, ordered.summary)
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(("pilot:A",), tuple(session.pending_principals()))
        self.assertEqual("graveyard", session.state.cards[detective.object_id].zone)
        applied = session.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(applied.ok, applied.summary)
        self.settle(session)
        self.assertEqual("hand", session.state.cards[detective.object_id].zone)
        self.assertEqual(1, session.state.cards[widow.object_id].counters.get("+1/+1"))
        designation = PreparedDesignation.from_dict(session.state.cards[dusk.object_id].annotations[PREPARED_FIELD])
        self.assertEqual("Vandal's Edit", session.engine.display_name(designation.copy_id))
        self.assertEqual(a_draws + 2, session.state.players["A"].stats["cards_drawn_by_turn"][turn])
        self.replay(session, load=True)

    def test_any_player_second_draw_includes_the_controller_once(self):
        session = self.session(121_832_002)
        king = self.add(session.engine, "King T'Challa", zone="hand")
        mastermind = self.add(session.engine, "Faerie Mastermind")
        brainstorm = self.add(session.engine, "Brainstorm", zone="hand")
        drawn = self.top_cards(session)
        for source in (king, mastermind, brainstorm):
            self.assert_closed(session, source)
        self.prepare(session, mana={"C": 5, "W": 2, "U": 3})
        self.checkpoint(session)
        offer = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                     if row["id"] == f"cast:{king.ref}")
        cast = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto", "reserve": {"C": 4, "W": 1, "U": 2}})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[king.object_id].zone)
        self.assertEqual("King T'Challa", session.state.cards[king.object_id].active_face)
        turn = str(session.state.turn_sequence)
        before = session.state.players["A"].stats.get("cards_drawn_by_turn", {}).get(turn, 0)
        self.assertEqual(0, before)
        self.cast_for(session, "A", brainstorm)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        session = self._choose_hand_top(session, [drawn[2].ref, drawn[0].ref])
        self.assertEqual(1, self.trigger_count(session, king))
        self.assertEqual(0, self.trigger_count(session, mastermind))
        self.settle(session)
        self.assertEqual(4, session.state.players["A"].stats["cards_drawn_by_turn"][turn])
        current = session.state.cards[king.object_id]
        identity = current.logical_object_id
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(action for action in actions
                     if action["id"].startswith(f"activate:{king.ref}:") and "Transform" in action["label"])
        activated = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[king.object_id]
        self.assertEqual("Black Panther, Hope Enduring", current.active_face)
        self.assertEqual(identity, current.logical_object_id)
        self.assertEqual(1, current.transform_count)
        self.replay(session, load=True)


    def _seed_private_order_hand(self, session, seat, names):
        for object_id in tuple(session.state.players[seat].zones["hand"]):
            session.engine.move_card(object_id, "library", log=False)
        return [self.add(session.engine, name, seat=seat, zone="hand", ref=f"affected-top-{seat}-{index}")
                for index, name in enumerate(names)]

    def _assert_private_order_choice(self, session, seat, refs):
        self.assertEqual("choice.apnap", session.state.pending_decision.kind)
        self.assertEqual([f"pilot:{seat}"], list(session.pending_principals()))
        packet = session.packet(f"pilot:{seat}", full=True)["decision"]
        field, = packet["legal_actions"][0]["form"]["fields"]
        self.assertTrue(field["ordered"])
        self.assertEqual(set(refs), {option["value"] for option in field["options"]})
        for other in set("ABCD") - {seat}:
            view = session.packet(f"pilot:{other}", full=True)
            self.assertIsNone(view["decision"])
            for ref in refs:
                self.assertNotIn(json.dumps(ref), json.dumps(view))

    def _order_hand_top_refs(self, session, seat, count):
        ids = session.state.players[seat].zones["library"]
        return [session.state.cards[object_id].ref for object_id in reversed(ids[-count:])]

    def test_actual_rats_and_imp_bind_the_announced_opponent_hand(self):
        for index, name in enumerate(("Chittering Rats", "Chimney Imp")):
            with self.subTest(name=name):
                session = self.session(401_830_020 + index)
                hand = self._seed_private_order_hand(session, "B", ("Shock", "Memnite"))
                source = self.add(session.engine, name, zone="battlefield" if index else "hand")
                spell = self.add(session.engine, "Murder", zone="hand") if index else source
                for card in (source, spell):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"C": 1, "B": 2})
                self.checkpoint(session)
                self.cast(session, "A", spell, [source.ref] if index else ())
                self.until(session, lambda game: game.state.pending_decision is not None
                           and game.state.pending_decision.kind == "semantic.target")
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": ["A"]}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.target(session, "A", ["B"])
                self.assertEqual("choice.apnap", self.resolve(session).kind)
                self._assert_private_order_choice(session, "B", [card.ref for card in hand])
                result = session.act("pilot:B", {"action_id": "choose", "cards": [hand[1].ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual([hand[1].ref], self._order_hand_top_refs(session, "B", 1))
                self.assertEqual("hand", session.state.cards[hand[0].object_id].zone)
                self.replay(session, load=True)

    def test_actual_prying_questions_loses_life_before_the_private_choice_and_accepts_empty_hand(self):
        for count in (0, 1):
            with self.subTest(hand_size=count):
                session = self.session(401_830_024 + count)
                hand = self._seed_private_order_hand(session, "B", ("Shock",)[:count])
                source = self.add(session.engine, "Prying Questions", zone="hand")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 2, "B": 1})
                self.checkpoint(session)
                original = list(session.state.players["B"].zones["library"])
                self.cast(session, "A", source, ["B"])
                pending = self.resolve(session)
                self.assertEqual(37, session.state.players["B"].life)
                if not count:
                    self.assertIsNone(pending)
                    self.assertEqual(original, session.state.players["B"].zones["library"])
                else:
                    self._assert_private_order_choice(session, "B", [hand[0].ref])
                    choice = {"action_id": "choose", "cards": [hand[0].ref]}
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act("pilot:A", choice).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    accepted = session.act("pilot:B", choice)
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertEqual([hand[0].ref], self._order_hand_top_refs(session, "B", 1))
                self.replay(session, load=True)

    def test_actual_stunted_growth_orders_a_short_hand_and_resumes_a_destination_replacement(self):
        for replacement in (False, True):
            with self.subTest(commander_replacement=replacement):
                session = self.session(401_830_030 + replacement)
                hand = self._seed_private_order_hand(session, "B", ("Shock",) if replacement else ("Shock", "Memnite"))
                if replacement:
                    player = session.state.players["B"]
                    commander_id = player.zones["command"].pop()
                    commander = session.state.cards[commander_id]
                    commander.zone = "hand"
                    commander.known_to = ["B"]
                    commander.revealed_to = []
                    player.zones["hand"].append(commander_id)
                    hand.append(commander)
                source = self.add(session.engine, "Stunted Growth", zone="hand")
                foreign = self.add(session.engine, "Forest", seat="D", zone="hand", ref="foreign-order-control")
                self.assert_closed(session, source)
                self.prepare(session, mana={"C": 3, "G": 2})
                self.checkpoint(session)
                self.cast(session, "A", source, ["B"])
                self.assertEqual("choice.apnap", self.resolve(session).kind)
                refs = [hand[1].ref, hand[0].ref]
                self._assert_private_order_choice(session, "B", refs)
                before = authoritative_state_hash(session.state)
                for invalid in ([refs[0]], [refs[0], refs[0]], [refs[0], foreign.ref]):
                    denied = session.act("pilot:B", {"action_id": "choose", "cards": invalid})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                accepted = session.act("pilot:B", {"action_id": "choose", "cards": refs})
                self.assertTrue(accepted.ok, accepted.summary)
                if replacement:
                    self.assertEqual("replacement.order", session.state.pending_decision.kind)
                    self.assertEqual(["pilot:B"], list(session.pending_principals()))
                    self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in hand))
                    session = self.replay(session, load=True)
                    options = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                    option = next(row for row in options if not row.get("decline"))
                    result = session.act("pilot:B", {"action_id": "choose", "replacement": option["id"]})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("command", session.state.cards[commander_id].zone)
                    refs = refs[1:]
                self.settle(session)
                self.assertEqual(refs, self._order_hand_top_refs(session, "B", len(refs)))
                for viewer in "ACD":
                    self.assertNotIn(json.dumps(hand[0].ref), json.dumps(session.packet(f"pilot:{viewer}", full=True)))
                self.replay(session, load=True)

    def test_actual_augermage_collects_private_apnap_choices_before_any_library_move(self):
        session = self.session(401_830_035)
        hands = {seat: self._seed_private_order_hand(session, seat, ("Shock",) if seat != "B" else ())
                 for seat in "ABCD"}
        source = self.add(session.engine, "Sadistic Augermage", seat="C")
        removal = self.add(session.engine, "Murder", zone="hand")
        for card in (source, removal):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 1, "B": 2})
        self.checkpoint(session)
        self.cast(session, "A", removal, [source.ref])
        self.assertEqual("choice.apnap", self.resolve(session).kind)
        for seat in "ACD":
            selected = hands[seat][0]
            self._assert_private_order_choice(session, seat, [selected.ref])
            ctx = session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]
            self.assertEqual({}, ctx["prior_public_choices"])
            for prior in set("ACD") - {seat}:
                self.assertNotIn(hands[prior][0].ref, json.dumps(ctx))
            self.assertTrue(all(session.state.cards[hand[0].object_id].zone == "hand"
                                for hand in hands.values() if hand))
            if seat == "C":
                session = self.replay(session, load=True)
            result = session.act(f"pilot:{seat}", {"action_id": "choose", "cards": [selected.ref]})
            self.assertTrue(result.ok, result.summary)
        self.settle(session)
        for seat in "ACD":
            ref = hands[seat][0].ref
            self.assertEqual([ref], self._order_hand_top_refs(session, seat, 1))
            for viewer in set("ABCD") - {seat}:
                self.assertNotIn(json.dumps(ref), json.dumps(session.packet(f"pilot:{viewer}", full=True)))
        self.replay(session, load=True)
