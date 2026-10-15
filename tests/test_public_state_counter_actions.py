from __future__ import annotations

import unittest
from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_original_fangren_and_fumes_current_attacking_counter_sets_replay(self):
        from quorune.model import CombatState
        session=self.session(3632901);engine=session.engine
        source=self.add(engine,'Fangren Firstborn',ref='combat-counter-fangren')
        body=self.add(engine,'Generic Bound Body',ref='combat-counter-vigilant')
        spectator=self.add(engine,'Generic Bound Body',ref='combat-counter-spectator');spectator.tapped=True
        fumes=self.add(engine,'Choking Fumes',zone='hand',ref='combat-counter-fumes')
        self.assert_whole_admission(source.printed_name);self.assert_whole_admission(fumes.printed_name)
        self.ready(session,fumes,{'C':2,'W':1})
        session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[]
        session.state.phase='combat';session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
        engine.permissions.invalidate_current();engine.pump();self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B',body.ref:'D'}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(1,session.state.cards[body.object_id].counters.get('+1/+1',0))
        self.assertEqual(0,session.state.cards[spectator.object_id].counters.get('+1/+1',0))
        while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==fumes.ref)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':cast['id'],'pay':'auto'}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        # CR704.5q removes matching +1/+1 and -1/-1 counters from attackers.
        for card in (source,body):
            current=session.state.cards[card.object_id]
            self.assertEqual(0,current.counters.get('+1/+1',0));self.assertEqual(0,current.counters.get('-1/-1',0))
        self.assertEqual(0,session.state.cards[spectator.object_id].counters.get('-1/-1',0))
        self.assertFalse(session.state.cards[body.object_id].tapped)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_source_damage_counter_survival_lethal_and_reentered_incarnation_replay(self):
        cases=((3632801,'Fungusaur',False,False),(3632802,'Siegehorn Ceratops',False,False),
            (3632803,'Siegehorn Ceratops',True,False),(3632804,'Siegehorn Ceratops',False,True))
        for seed,name,lethal,reenter in cases:
            with self.subTest(name=name,lethal=lethal,reenter=reenter):
                session=self.session(seed);engine=session.engine
                source=self.add(engine,name,ref='source-damage-counter-recipient')
                damage=self.add(engine,'Lightning Bolt' if lethal else 'Generic Source Counter One Damage',zone='hand',ref='source-damage-counter-damage')
                bounce=self.add(engine,'Unsummon',zone='hand',ref='source-damage-counter-bounce')
                self.assert_whole_admission(source.printed_name)
                offered=self.ready(session,damage,{'R':1,'U':1,'G':1,'W':1})
                self.checkpoint(session);identity=source.logical_object_id
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                for _ in range(8):
                    if any(item.kind=='triggered_ability' for item in session.state.stack):break
                    self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
                self.replay(session,load=True)
                if reenter:
                    while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    offered=next(row for row in actions if row.get('card')==bounce.ref)
                    self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'}).ok)
                    self.resolve(session)
                    while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    offered=next(row for row in actions if row.get('card')==source.ref)
                    self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
                self.resolve(session)
                current=session.state.cards[source.object_id]
                self.assertEqual('graveyard' if lethal else 'battlefield',current.zone)
                self.assertEqual(0 if lethal or reenter else 1 if name=='Fungusaur' else 2,current.counters.get('+1/+1',0))
                if reenter:self.assertNotEqual(identity,current.logical_object_id)
                for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_gleam_attacker_and_rakish_damage_source_counter_recipients_replay(self):
        from quorune.model import CombatState
        for seed,name in ((3632701,'Gleam of Battle'),(3632702,'Rakish Heir'),(3632703,'Necropolis Regent')):
            with self.subTest(name=name):
                session=self.session(seed);engine=session.engine
                observer=self.add(engine,name,ref='action-counter-observer')
                attacker=self.add(engine,'Generic Event Counter Vampire',ref='action-counter-attacker')
                control=self.add(engine,'Generic Bound Body',ref='action-counter-control')
                self.assert_whole_admission(observer.printed_name)
                engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
                session.state.active_player='A';session.state.phase='combat';session.state.step='declare_attackers'
                session.state.phase_index=5;session.state.priority_player=None;session.state.priority_passes=[];session.state.combat=CombatState()
                engine.pump();self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{attacker.ref:'B',control.ref:'D'}}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B',control.ref:'D'}})
                self.assertTrue(accepted.ok,accepted.summary)
                checked=False
                for _ in range(70):
                    if session.state.phase=='postcombat_main' and not session.state.stack:break
                    decision=session.state.pending_decision
                    if decision.kind=='trigger.order':
                        pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
                        self.replay(session,load=True);checked=True
                        command={'action_id':'order','triggers':[item.ref for item in pending]}
                    elif decision.kind=='priority':
                        if any(item.kind=='triggered_ability' for item in session.state.stack) and not checked:
                            self.replay(session,load=True);checked=True
                        command={'action_id':'pass'}
                    elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
                    else:self.fail('Unexpected eventcounter decision '+decision.kind)
                    accepted=session.act(session.pending_principals()[0],command);self.assertTrue(accepted.ok,accepted.summary)
                else:self.fail('Actioneventcountercombatdidnotfinish')
                self.assertTrue(checked)
                self.assertEqual(2 if name=='Necropolis Regent' else 1,session.state.cards[attacker.object_id].counters.get('+1/+1',0))
                self.assertEqual(2 if name=='Necropolis Regent' else 1 if name=='Gleam of Battle' else 0,session.state.cards[control.object_id].counters.get('+1/+1',0))
                self.assertEqual(0,session.state.cards[observer.object_id].counters.get('+1/+1',0))
                for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_unicorn_counter_entry_recipient_and_pending_bounce_reentry_replay(self):
        for seed,reenter in ((3632601,False),(3632602,True)):
            with self.subTest(reenter=reenter):
                session=self.session(seed);engine=session.engine
                observer=self.add(engine,'Good-Fortune Unicorn',ref='entrycounter-unicorn')
                body=self.add(engine,'Generic Ordinary Grant Flash Sliver',zone='hand',ref='entrycounter-recipient')
                bounce=self.add(engine,'Unsummon',zone='hand',ref='entrycounter-bounce')
                self.assert_whole_admission(observer.printed_name)
                offered=self.ready(session,body,{'U':1})
                self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'pay':'auto'}).ok);self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
                for _ in range(8):
                    if any(item.kind=='triggered_ability' for item in session.state.stack):break
                    self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
                identity=session.state.cards[body.object_id].logical_object_id
                self.replay(session,load=True)
                if reenter:
                    while session.state.priority_player!='A':
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==bounce.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[body.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary)
                    for _ in range(8):
                        if session.state.cards[body.object_id].zone=='hand':break
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    while session.state.priority_player!='A':
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==body.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
                self.resolve(session)
                current=session.state.cards[body.object_id]
                self.assertEqual(1,current.counters.get('+1/+1',0));self.assertEqual(0,session.state.cards[observer.object_id].counters.get('+1/+1',0))
                if reenter:self.assertNotEqual(identity,current.logical_object_id)
                for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session)
