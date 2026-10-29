"""Actual entry, land, activated, optional and spell ordering compositions."""
import unittest
from dataclasses import replace
import top_order_fixture as fixtures


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
