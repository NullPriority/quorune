"""Native inherited compositions partitioned by the existing loadfile granularity."""
import json,unittest
from test_qualified_zone_event_queries import query_record
import inherited_composition_fixture as fixtures
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry

class DeathLocusCompositionNativeTests(fixtures.InheritedNativeFixture, unittest.TestCase):
    def test_actual_death_damage_uses_departing_controller_and_preserves_attached_attack_rule(self):
        session=self.session(608646001);staff=self.add(session.engine,'Dingus Staff');gimli=self.add(session.engine,'Gimli, Counter of Kills');aura=self.add(session.engine,'Impending Doom',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',seat='C',ref='death-role-body');session.engine.change_control(body.object_id,'B',reason='Prepared C-owned B-controlled victim');body.acquired_control_turn_count=-1
        own=self.add(session.engine,'Grizzly Bears',ref='death-role-own');first=self.add(session.engine,'Doom Blade',zone='hand',ref='death-role-first');second=self.add(session.engine,'Doom Blade',zone='hand',ref='death-role-second');self.add(session.engine,'Vedalken Orrery')
        private=self.add(session.engine,'Shock',seat='D',zone='hand',ref='death-role-private')
        for i in range(4):self.add(session.engine,'Swamp',ref='death-role-payment-'+str(i))
        for card in (staff,gimli,aura):self.assert_closed(session,card)
        self.prepare(session,active='B',mana={'C':4,'R':1,'B':2});self.checkpoint(session);self.cast_for(session,'A',aura,[body.ref],reserve={'C':2,'B':2});self.settle(session)
        self.assertEqual((5,5),tuple(session.engine._numeric_stat(body.object_id,k) for k in ('power','toughness')))
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers');before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'attack','attackers':{}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'attack','attackers':{body.ref:'D'}});self.assertTrue(result.ok,result.summary)
        life={s:session.state.players[s].life for s in 'ABCD'};self.cast(session,'A',first,[body.ref]);self.settle(session)
        self.assertEqual(life['B']-6,session.state.players['B'].life)
        for seat in 'ACD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.assertEqual('graveyard',session.state.cards[aura.object_id].zone);self.assertIn(body.object_id,session.state.players['C'].zones['graveyard'])
        before_life={s:session.state.players[s].life for s in 'ABCD'};self.cast(session,'A',second,[own.ref]);self.settle(session)
        self.assertEqual(before_life['A']-2,session.state.players['A'].life)
        for seat in 'BCD':self.assertEqual(before_life[seat],session.state.players[seat].life)
        for seat in 'ABC':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_glimmerpost_counts_current_loci_across_seats_at_resolution(self):
        for removed in (False,True):
            with self.subTest(removed=removed):
                session=self.session(608646050+removed);source=self.add(session.engine,'Glimmerpost',zone='hand');self.add(session.engine,'Cloudpost',seat='B');self.add(session.engine,'Island',seat='C')
                spell=self.add(session.engine,'Stone Rain',seat='B',zone='hand');self.add(session.engine,'Vedalken Orrery',seat='B')
                for i in range(3):self.add(session.engine,'Mountain',seat='B',ref='post-response-'+str(i))
                self.assert_closed(session,source);self.prepare(session);self.checkpoint(session);self.priority(session,'A');life=session.state.players['A'].life
                result=session.act('pilot:A',{'action_id':'play-land:'+source.ref});self.assertTrue(result.ok,result.summary);self.assertEqual(1,self.trigger_count(session,source))
                if removed:self.cast(session,'B',spell,[source.ref]);self.resolve_top(session)
                session=self.replay(session,load=True);self.settle(session);self.assertEqual(life+(1 if removed else 2),session.state.players['A'].life);self.replay(session,load=True)

    def test_actual_cloudpost_enters_tapped_and_pays_current_global_locus_quantity_for_its_controller(self):
        session=self.session(608646065);source=self.add(session.engine,'Cloudpost',seat='B',zone='hand');self.add(session.engine,'Glimmerpost');self.add(session.engine,'Island');untap=self.add(session.engine,'Twiddle',zone='hand')
        self.assert_closed(session,source);self.prepare(session,actor='B',active='B');self.checkpoint(session)
        result=session.act('pilot:B',{'action_id':'play-land:'+source.ref});self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertFalse(any(a['id'].startswith('activate:'+source.ref+':') for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']))
        self.cast(session,'A',untap,[source.ref]);self.assertEqual('semantic.choice',self.resolve(session).kind);result=session.act('pilot:A',{'action_id':'choose','choice':'untap'});self.assertTrue(result.ok,result.summary);self.settle(session);self.priority(session,'B')
        offered=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
        for principal,bundle in (('pilot:C',{'C':2}),('pilot:B',{'C':1})):
            before=authoritative_state_hash(session.state);result=session.act(principal,{'action_id':offered['id'],'mana_output':bundle,'pay':'auto'});self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':offered['id'],'mana_output':{'C':2},'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertEqual(2,session.state.players['B'].mana_pool['C']);self.assertTrue(session.state.cards[source.object_id].tapped);self.replay(session,load=True)

