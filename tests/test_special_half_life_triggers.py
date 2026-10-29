"""An attack and later combat damage read distinct current life totals."""
import json,unittest
import special_trigger_fixture as fixtures

class SpecialHalfLifeNativeTests(fixtures.SpecialTriggerFixture,unittest.TestCase):
    def test_actual_face_up_reaper_uses_current_life_then_post_damage_opponent_life(self):
        session=self.session(610650050);source=self.add(session.engine,'Ebonblade Reaper',zone='hand');heal=self.add(session.engine,'Healing Salve',zone='hand');self.add(session.engine,'Plains');self.add(session.engine,'Fervor');self.assert_closed(session,source)
        self.prepare(session,mana={'C':2,'B':1});session.state.players['A'].life=41;session.state.players['B'].life=43;self.checkpoint(session);self.cast(session,'A',source);self.settle(session);self.attack(session,[(source,'B')]);self.assertEqual(1,self.trigger_count(session,source));self.assertEqual(41,session.state.players['A'].life)
        self.priority(session,'A');offered=self.action(session,'A',heal);result=session.act('pilot:A',{'action_id':offered['id'],'modes':['mode_1'],'targets':['A'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve_top(session);self.assertEqual(44,session.state.players['A'].life);self.settle(session);self.assertEqual(22,session.state.players['A'].life)
        self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority');self.settle(session);self.assertEqual(21,session.state.players['B'].life)
        for seat in 'CD':self.assertEqual(40,session.state.players[seat].life)
        self.replay(session,load=True)

    def test_actual_morph_attack_has_no_life_loss_but_later_turn_up_restores_damage_trigger(self):
        session=self.session(610650060);source=self.add(session.engine,'Ebonblade Reaper',zone='hand');self.add(session.engine,'Fervor')
        for i,name in enumerate(('Wastes','Wastes','Wastes','Swamp','Swamp')):self.add(session.engine,name,ref='morph-mana-'+str(i))
        self.assert_closed(session,source);self.prepare(session,mana={'C':3});session.state.players['A'].life=41;session.state.players['B'].life=43;self.checkpoint(session);offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='cast-morph:'+source.ref);result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.settle(session)
        self.assertTrue(session.state.cards[source.object_id].face_down);self.attack(session,[(source,'B')]);self.assertEqual(0,self.trigger_count(session,source));self.assertEqual(41,session.state.players['A'].life)
        for seat in 'BCD':self.assertNotIn('Ebonblade Reaper',json.dumps(session.packet('pilot:'+seat,full=True)))
        self.priority(session,'A');offered=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('turn-face-up:'+source.ref));result=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertFalse(session.state.cards[source.object_id].face_down);self.assertEqual(0,self.trigger_count(session,source));session=self.replay(session,load=True)
        self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority');self.settle(session);self.assertEqual(41,session.state.players['A'].life);self.assertEqual(21,session.state.players['B'].life);self.replay(session,load=True)
