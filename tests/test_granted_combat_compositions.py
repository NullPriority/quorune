"""Native inherited compositions partitioned by the existing loadfile granularity."""
import json,unittest
from test_qualified_zone_event_queries import query_record
import inherited_composition_fixture as fixtures
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry

class GrantedCombatCompositionNativeTests(fixtures.InheritedNativeFixture, unittest.TestCase):
    def test_actual_sinstriker_grant_uses_recipient_controller_power_and_attacking_target(self):
        session=self.session(608646060);aura=self.add(session.engine,"Sinstriker's Will",zone='hand');recipient=self.add(session.engine,'Grizzly Bears',seat='B',ref='sinstriker-recipient');recipient.counters['+1/+1']=1;recipient.acquired_control_turn_count=-1
        target=self.add(session.engine,'Shivan Dragon',seat='C',ref='sinstriker-attacker');target.acquired_control_turn_count=-1;idle=self.add(session.engine,'Grizzly Bears',seat='C',ref='sinstriker-idle')
        pump=self.add(session.engine,'Giant Growth',seat='B',zone='hand');self.add(session.engine,'Forest',seat='B');remove=self.add(session.engine,'Disenchant',zone='hand');self.add(session.engine,'Vedalken Orrery')
        for i in range(4):self.add(session.engine,'Plains',ref='sinstriker-removal-land-'+str(i))
        self.assert_closed(session,aura);self.prepare(session,active='C',mana={'C':3,'W':1});self.checkpoint(session);self.cast(session,'A',aura,[recipient.ref]);self.settle(session)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:C',{'action_id':'attack','attackers':{target.ref:'A'}});self.assertTrue(result.ok,result.summary);self.priority(session,'B')
        offered=next(a for a in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+recipient.ref+':'))
        for seat,selected in (('A',target),('B',idle)):
            before=authoritative_state_hash(session.state);result=session.act('pilot:'+seat,{'action_id':offered['id'],'targets':[selected.ref],'pay':'auto'});self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':offered['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[recipient.object_id].tapped)
        self.cast(session,'A',remove,[aura.ref]);self.resolve_top(session);self.assertEqual('graveyard',session.state.cards[aura.object_id].zone)
        self.cast(session,'B',pump,[recipient.ref]);self.resolve_top(session);self.assertEqual(6,session.engine._numeric_stat(recipient.object_id,'power'))
        session=self.replay(session,load=True);self.settle(session);self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertEqual('battlefield',session.state.cards[idle.object_id].zone)
        dealt=next(e for e in reversed(session.state.turn_history.events) if e.kind=='permanent_damaged' and e.target==target.ref)
        self.assertEqual((6,'B',session.state.cards[recipient.object_id].logical_object_id),(dealt.amount,dealt.actor,dealt.object_incarnation));self.replay(session,load=True)

    def test_actual_ichor_threshold_keeps_new_block_illegal_but_preserves_an_existing_block(self):
        for fourth_before_block in (False,True):
            with self.subTest(fourth_before_block=fourth_before_block):
                session=self.session(608646070+fourth_before_block);source=self.add(session.engine,'Ichor Synthesizer',zone='hand');spells=[self.add(session.engine,'Reach Through Mists',zone='hand',ref='oil-spell-'+str(i)) for i in range(4)]
                blocker=self.add(session.engine,'Grizzly Bears',seat='B');self.add(session.engine,'Fervor');self.add(session.engine,'Island');self.assert_closed(session,source)
                self.prepare(session,mana={'C':1,'U':5});self.checkpoint(session);self.cast_for(session,'A',source,reserve={'U':4});self.settle(session)
                for index in range(3):self.cast_for(session,'A',spells[index],reserve={'U':3-index});self.settle(session)
                self.assertEqual(3,session.state.cards[source.object_id].counters.get('oil'));self.assertEqual((1,3),tuple(session.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
                if fourth_before_block:self.cast(session,'A',spells[3]);self.settle(session)
                life=session.state.players['B'].life;self.attack(session,[(source,'B')])
                command={'action_id':'block','blocks':{blocker.ref:source.ref}}
                if fourth_before_block:
                    def pass_no_legal_blockers(s):
                        if s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers':
                            before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:B',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
                        return s.state.phase=='postcombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority'
                    self.until(session,pass_no_legal_blockers)
                else:
                    self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
                    result=session.act('pilot:B',command);self.assertTrue(result.ok,result.summary);self.cast(session,'A',spells[3]);self.settle(session)
                self.assertEqual(4,session.state.cards[source.object_id].counters.get('oil'));self.assertEqual((3,3),tuple(session.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
                self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority')
                self.assertEqual(life-(3 if fourth_before_block else 0),session.state.players['B'].life);self.replay(session,load=True)

    def test_actual_glory_chaser_gains_renown_then_requires_two_blockers_in_later_combat(self):
        from quorune.model import TurnEntry
        session=self.session(608646080);source=self.add(session.engine,'Goblin Glory Chaser',zone='hand');self.add(session.engine,'Fervor')
        blockers=[self.add(session.engine,'Grizzly Bears',seat='B',ref='glory-blocker-'+str(i)) for i in range(2)]
        self.assert_closed(session,source);self.prepare(session,mana={'R':1});self.checkpoint(session);self.cast(session,'A',source);self.settle(session);self.assertFalse(session.state.cards[source.object_id].renowned)
        self.assertNotIn('menace',session.engine._combat_keywords(session.state.cards[source.object_id]));self.attack(session,[(source,'B')]);self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        result=session.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.pending_decision is not None and s.state.pending_decision.kind=='priority')
        current=session.state.cards[source.object_id];self.assertTrue(current.renowned);self.assertEqual(1,current.counters.get('+1/+1'));self.assertIn('menace',session.engine._combat_keywords(current));self.replay(session,load=True)
        # Prepare the canonical beginning of a later controller turn. This
        # proves persistent Renown, not a new extra-turn or extra-combat card.
        engine=session.engine;engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[]
        engine._begin_turn(TurnEntry(turn_id='prepared-renowned-turn',player='A',created_sequence=session.state.turn_sequence));engine.pump();self.checkpoint(session)
        self.attack(session,[(session.state.cards[source.object_id],'B')]);self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:B',{'action_id':'block','blocks':{blockers[0].ref:source.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:B',{'action_id':'block','blocks':{b.ref:source.ref for b in blockers}});self.assertTrue(result.ok,result.summary);self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1'));self.replay(session,load=True)
