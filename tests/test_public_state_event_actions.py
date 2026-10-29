from __future__ import annotations

import unittest
from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_original_polluted_bonds_land_controller_and_campaign_split_defenders_replay(self):
        session=self.session(3632501);engine=session.engine
        observer=self.add(engine,'Polluted Bonds',seat='B',ref='event-controller-bonds')
        self.assert_whole_admission(observer.printed_name)
        setup=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='event-controller-setup')
        self.ready(session,setup,{})
        land=next(session.state.cards[oid] for oid in session.state.players['A'].zones['hand'] if 'Land' in engine.card_record(session.state.cards[oid]).type_line)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('play-land:') and row.get('card')==land.ref)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        self.checkpoint(session);before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id']}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(life['A']-2,session.state.players['A'].life);self.assertEqual(life['B']+2,session.state.players['B'].life)
        for seat in 'CD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.replay(session)
        from quorune.model import CombatState
        session=self.session(3632502);engine=session.engine
        self.add(engine,'Campaign of Vengeance',ref='event-controller-campaign')
        attackers=[self.add(engine,'Generic Bound Body',ref='event-controller-attacker-'+str(i)) for i in range(2)]
        self.assert_whole_admission('Campaign of Vengeance')
        engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
        session.state.active_player='A';session.state.phase='combat';session.state.step='declare_attackers'
        session.state.phase_index=5;session.state.priority_player=None;session.state.priority_passes=[];session.state.combat=CombatState()
        engine.pump();self.checkpoint(session)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attackers[0].ref:'B',attackers[1].ref:'C'}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('trigger.order',session.state.pending_decision.kind)
        pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2,len(pending))
        self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':'order','triggers':[item.ref for item in reversed(pending)]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(life['A']+2,session.state.players['A'].life)
        self.assertEqual(life['B']-1,session.state.players['B'].life);self.assertEqual(life['C']-1,session.state.players['C'].life)
        self.assertEqual(life['D'],session.state.players['D'].life)
        self.replay(session)

    def test_original_colored_sphere_prevention_actual_damage_contrasts_and_departure_replay(self):
        session=self.session(3632401);engine=session.engine
        sphere=self.add(engine,'Sphere of Law',zone='hand',ref='colored-prevention-sphere')
        red=[self.add(engine,'Lightning Bolt',zone='hand',ref='colored-prevention-red-'+str(i)) for i in range(3)]
        blue=self.add(engine,'Generic Colored Prevention Blue Damage',zone='hand',ref='colored-prevention-blue')
        remove=self.add(engine,'Disenchant',zone='hand',ref='colored-prevention-remove')
        self.assert_whole_admission(sphere.printed_name)
        offered=self.ready(session,sphere,{'C':6,'W':2,'R':6})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        def cast(card,target):
            while session.state.priority_player!='A':
                self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            self.assertTrue(any(row.get('card')==card.ref for row in actions),
                {'card':card.printed_name,'pool':dict(session.state.players['A'].mana_pool),'zone':session.state.cards[card.object_id].zone})
            offered=next(row for row in actions if row.get('card')==card.ref)
            accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[target],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        cast(red[0],'A');self.assertEqual(life['A']-1,session.state.players['A'].life)
        cast(blue,'A');self.assertEqual(life['A']-4,session.state.players['A'].life)
        cast(red[1],'B');self.assertEqual(life['B']-3,session.state.players['B'].life)
        cast(remove,sphere.ref);self.assertEqual('graveyard',session.state.cards[sphere.object_id].zone)
        self.replay(session,load=True)
        cast(red[2],'A');self.assertEqual(life['A']-7,session.state.players['A'].life)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_event_player_two_result_kambal_and_assassin_native_actions_replay(self):
        session=self.session(3632301);engine=session.engine
        observer=self.add(engine,'Kambal, Consul of Allocation',seat='B',ref='sequence-event-kambal')
        spell=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='sequence-event-noncreature')
        creature=self.add(engine,'Generic Bound Body',zone='hand',ref='sequence-event-creature')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,spell,{'W':1})
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        trigger=next(item for item in session.state.stack if item.kind=='triggered_ability')
        self.assertEqual('B',trigger.controller)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(life['A'],session.state.players['A'].life)
        self.assertEqual(life['B']+2,session.state.players['B'].life)
        for seat in 'CD':self.assertEqual(life[seat],session.state.players[seat].life)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==creature.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertFalse(any(item.kind=='triggered_ability' for item in session.state.stack))
        self.resolve(session)
        self.assertEqual(life['B']+2,session.state.players['B'].life)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)
        from quorune.model import CombatState
        session=self.session(3632302);engine=session.engine
        source=self.add(engine,'Agate-Blade Assassin',ref='sequence-event-assassin')
        self.assert_whole_admission(source.printed_name)
        engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
        session.state.active_player='A';session.state.phase='combat';session.state.step='declare_attackers'
        session.state.phase_index=5;session.state.priority_player=None;session.state.priority_passes=[];session.state.combat=CombatState()
        engine.pump();self.checkpoint(session)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{source.ref:'B'}}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(life['A']+1,session.state.players['A'].life)
        self.assertEqual(life['B']-1,session.state.players['B'].life)
        for seat in 'CD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.replay(session)

    def test_original_forced_fruition_caster_draw_and_caress_discarder_life_loss_replay(self):
        session=self.session(3632201);engine=session.engine
        observer=self.add(engine,'Forced Fruition',seat='B',ref='event-result-fruition')
        spell=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='event-result-fruition-cast')
        # Refill a larger known initial library so this witness exercises seven draws.
        for index in range(8):self.add(engine,'Generic Bound Plains',zone='library',ref='event-result-extra-library-'+str(index))
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,spell,{})
        hand=len(session.state.players['A'].zones['hand'])
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'targets':['A'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(hand+6,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)
        session=self.session(3632202);engine=session.engine
        observer=self.add(engine,"Liliana's Caress",ref='event-result-caress')
        discard=self.add(engine,'Mind Rot',zone='hand',ref='event-result-caress-discard')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,discard,{'C':2,'B':1})
        life=session.state.players['B'].life
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);decision=self.resolve(session)
        self.assertIsNotNone(decision)
        cards=[session.state.cards[oid].ref for oid in session.state.players['B'].zones['hand'][:2]]
        accepted=session.act('pilot:B',{'action_id':'choose','cards':cards})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True)
        for _ in range(12):
            if session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
                pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
                accepted=session.act('pilot:A',{'action_id':'order','triggers':[item.ref for item in pending]})
                self.assertTrue(accepted.ok,accepted.summary)
            decision=self.resolve(session)
            if not session.state.stack and not session.state.pending_trigger_batches:break
        self.assertEqual(life-4,session.state.players['B'].life,
            {'decision':session.state.pending_decision.kind if session.state.pending_decision else None,
             'stack':[(item.kind,item.semantic_key) for item in session.state.stack],
             'discarded':[(ref,next(c.zone for c in session.state.cards.values() if c.ref==ref)) for ref in cards]})
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_event_bound_mill_uses_damaged_player_defender_and_caster_with_replay(self):
        from quorune.model import CombatState
        for index,name in enumerate(('Shriekgeist','Crosstown Courier','Nemesis of Reason')):
            with self.subTest(name=name):
                session=self.session(3632101+index);engine=session.engine
                source=self.add(engine,name,ref='event-bound-mill-source')
                self.assert_whole_admission(source.printed_name)
                engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
                session.state.active_player='A';session.state.priority_player=None;session.state.priority_passes=[]
                session.state.phase='combat';session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
                engine.pump();self.checkpoint(session)
                before={s:len(session.state.players[s].zones['graveyard']) for s in 'ABCD'}
                available=len(session.state.players['B'].zones['library'])
                expected=10 if name=='Nemesis of Reason' else 2
                denied=session.act('pilot:D',{'action_id':'attack','attackers':{source.ref:'B'}})
                self.assertFalse(denied.ok)
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                self.replay(session,load=True)
                for _ in range(60):
                    if session.state.phase=='postcombat_main' and not session.state.stack:break
                    decision=session.state.pending_decision
                    if decision.kind=='priority':command={'action_id':'pass'}
                    elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
                    else:self.fail('Unexpected boundmill decision '+decision.kind)
                    accepted=session.act(session.pending_principals()[0],command);self.assertTrue(accepted.ok,accepted.summary)
                else:self.fail('Boundmillcombatdidnotfinish')
                self.assertEqual(before['B']+min(expected,available),len(session.state.players['B'].zones['graveyard']))
                for seat in 'ACD':self.assertEqual(before[seat],len(session.state.players[seat].zones['graveyard']))
                self.replay(session)
        session=self.session(3632104);engine=session.engine
        observer=self.add(engine,'Memory Erosion',seat='B',ref='event-mill-caster-observer')
        spell=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='event-mill-cast-spell')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,spell,{})
        before={s:len(session.state.players[s].zones['graveyard']) for s in 'ABCD'}
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(before['A']+3,len(session.state.players['A'].zones['graveyard']))
        for seat in 'BCD':self.assertEqual(before[seat],len(session.state.players[seat].zones['graveyard']))
        self.replay(session)

    def test_original_psychic_corrosion_draw_mills_current_opponents_as_atomic_batches_and_replays(self):
        from unittest.mock import patch
        from quorune.zone_transitions import ZoneTransitionOwner
        session=self.session(3632001);engine=session.engine
        observer=self.add(engine,'Psychic Corrosion',zone='hand',ref='opponent-mill-corrosion')
        draw=self.add(engine,'Divination',zone='hand',ref='opponent-mill-draw')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,observer,{'C':4,'U':2})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==draw.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        while session.state.priority_player!='C':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        accepted=session.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(accepted.ok,accepted.summary)
        before={s:len(session.state.players[s].zones['graveyard']) for s in 'ABD'}
        available={s:len(session.state.players[s].zones['library']) for s in 'BD'}
        before_a_library=len(session.state.players['A'].zones['library'])
        self.replay(session,load=True)
        original=ZoneTransitionOwner.move_cards_simultaneously;groups=[]
        def capture(owner,moves,*args,**kwargs):
            moves=tuple(moves)
            if moves and all(destination=='graveyard' for _,destination in moves):
                groups.append(tuple(owner.host.state.cards[object_id].owner for object_id,_ in moves))
            return original(owner,moves,*args,**kwargs)
        with patch.object(ZoneTransitionOwner,'move_cards_simultaneously',new=capture):
            self.resolve(session)
            if session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
                pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
                accepted=session.act('pilot:A',{'action_id':'order','triggers':[item.ref for item in pending]})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(before['B']+min(4,available['B']),len(session.state.players['B'].zones['graveyard']))
        self.assertEqual(before['D']+min(4,available['D']),len(session.state.players['D'].zones['graveyard']))
        self.assertEqual(before['A']+1,len(session.state.players['A'].zones['graveyard']))
        self.assertEqual(before_a_library-2,len(session.state.players['A'].zones['library']))
        self.assertEqual(2,len(groups))
        for index,group in enumerate(groups):
            self.assertEqual(min(2,max(0,available['B']-2*index)),group.count('B'))
            self.assertEqual(min(2,max(0,available['D']-2*index)),group.count('D'))
            self.assertTrue(set(group)<={'B','D'})
        for seat in 'BD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_damage_amount_subscription_diagnostic_excludes_noncombat_incoming_and_foreign_sources(self):
        """Matcher diagnostic; actual combat supplies the command witness."""
        from quorune.trigger_discovery import semantic_event_matches
        session=self.session(3631904);engine=session.engine
        wall=self.add(engine,'Wall of Essence',ref='amount-diagnostic-wall')
        thrull=self.add(engine,'Mourning Thrull',ref='amount-diagnostic-thrull')
        other=self.add(engine,'Generic Bound Body',seat='B',ref='amount-diagnostic-other')
        wall_program=next(p for p in engine.semantics.programs_for_oracle(wall.oracle_id) if p.event=='combat.damage.received')
        thrull_program=next(p for p in engine.semantics.programs_for_oracle(thrull.oracle_id) if p.event=='damage.dealt.self')
        for combat,target,expected in ((True,wall.ref,True),(False,wall.ref,False),(True,other.ref,False)):
            context={'card':target,'source':other.ref,'target':target,'combat':combat,'amount':2,'target_kind':'permanent'}
            self.assertEqual(expected,semantic_event_matches(engine,wall_program,wall,'combat.damage.received',context))
        for card,expected in ((thrull.ref,True),(other.ref,False)):
            context={'card':card,'source':card,'target':wall.ref,'combat':False,'amount':1,'target_kind':'permanent'}
            self.assertEqual(expected,semantic_event_matches(engine,thrull_program,thrull,'damage.dealt',context))

    def test_original_committed_combat_damage_amount_draw_gain_and_receive_replay(self):
        from quorune.model import CombatState
        cases=((3631901,'Fear of Failed Tests',False),(3631902,'Mourning Thrull',False),(3631903,'Wall of Essence',True))
        for seed,name,blocking in cases:
            with self.subTest(name=name):
                session=self.session(seed);engine=session.engine
                source=self.add(engine,name,seat='B' if blocking else 'A',ref='committed-damage-amount-source')
                attacker=self.add(engine,'Generic Bound Body',ref='committed-damage-amount-attacker') if blocking else source
                self.assert_whole_admission(source.printed_name)
                self.ready(session,self.add(engine,'Generic Boolean Life Response',zone='hand',ref='committed-amount-setup'),{})
                session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[]
                session.state.phase='combat';session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
                engine.permissions.invalidate_current();engine.pump()
                before_hand=len(session.state.players['A'].zones['hand'])
                life={seat:session.state.players[seat].life for seat in 'ABCD'}
                self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{attacker.ref:'B'}}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                pending_checked=False
                for _ in range(70):
                    if any(item.kind=='triggered_ability' for item in session.state.stack) and not pending_checked:
                        self.replay(session,load=True);pending_checked=True
                    if session.state.phase=='postcombat_main' and not session.state.stack:break
                    decision=session.state.pending_decision
                    if decision.kind=='combat.blockers':
                        command={'action_id':'block','blocks':{source.ref:attacker.ref} if blocking else {}}
                    elif decision.kind=='priority':command={'action_id':'pass'}
                    else:self.fail('Unexpected combat decision '+decision.kind)
                    accepted=session.act(session.pending_principals()[0],command)
                    self.assertTrue(accepted.ok,accepted.summary)
                else:self.fail('Committed damage combat did not reach main phase')
                self.assertTrue(pending_checked)
                if name=='Fear of Failed Tests':
                    self.assertEqual(before_hand+2,len(session.state.players['A'].zones['hand']))
                    self.assertEqual(life['B']-2,session.state.players['B'].life)
                elif name=='Mourning Thrull':
                    self.assertEqual(life['A']+1,session.state.players['A'].life)
                    self.assertEqual(life['B']-1,session.state.players['B'].life)
                else:
                    self.assertEqual(life['B']+2,session.state.players['B'].life)
                    self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                for observer in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_source_attack_damage_and_granted_sliver_attack_target_revalidation_replay(self):
        from quorune.model import CombatState
        cases=((3631801,'Raging Regisaur',False),(3631802,'Thorncaster Sliver',False),
            (3631803,'Raging Regisaur',True),(3631804,'Thorncaster Sliver',True))
        for seed,name,revalidate in cases:
            with self.subTest(name=name,revalidate=revalidate):
                session=self.session(seed);engine=session.engine
                source=self.add(engine,name,ref='source-action-damage-observer')
                attacker=source if name=='Raging Regisaur' else self.add(engine,'Generic Ordinary Grant Sliver',ref='source-action-granted-attacker')
                victim=self.add(engine,'Generic Bound Body',seat='B',ref='source-action-damage-victim')
                bounce=self.add(engine,'Unsummon',zone='hand',ref='source-action-target-bounce')
                self.assert_whole_admission(source.printed_name)
                self.ready(session,bounce,{'U':1})
                session.state.pending_decision=None;session.state.priority_player=None
                session.state.priority_passes=[];session.state.phase='combat'
                session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
                engine.permissions.invalidate_current();engine.pump();self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{attacker.ref:'B'}}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                decision=session.state.pending_decision
                self.assertEqual('semantic.target',decision.kind)
                accepted=session.act('pilot:A',{'action_id':'choose','targets':[victim.ref]})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual(attacker.object_id,session.state.stack[-1].source_object_id)
                self.replay(session,load=True)
                if revalidate:
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==bounce.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[victim.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary)
                self.resolve(session)
                self.assertEqual('hand' if revalidate else 'battlefield',session.state.cards[victim.object_id].zone)
                self.assertEqual(0 if revalidate else 1,session.state.cards[victim.object_id].marked_damage)
                self.assertFalse(session.state.stack)
                for observer in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_dormant_sliver_entry_and_foreign_recipient_draws_replay(self):
        session=self.session(3631701);engine=session.engine
        source=self.add(engine,'Dormant Sliver',zone='hand',ref='ordinary-dormant-source')
        recipient=self.add(engine,'Generic Ordinary Grant Flash Sliver',seat='B',zone='hand',ref='ordinary-dormant-recipient')
        session.state.players['B'].mana_pool['C']=1
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'G':1,'U':1,'C':2})
        before_a=len(session.state.players['A'].zones['hand'])
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(before_a,len(session.state.players['A'].zones['hand']))
        while session.state.priority_player!='B':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==recipient.ref)
        before_b=len(session.state.players['B'].zones['hand'])
        accepted=session.act('pilot:B',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(before_b,len(session.state.players['B'].zones['hand']))
        self.assertIn('Defender',engine._effective_card_data(session.state.cards[recipient.object_id])['keywords'])
        for observer in 'ACD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['B'])
        self.replay(session)

    def test_original_clot_granted_regeneration_protects_recipient_after_actual_destroy_and_replays(self):
        session=self.session(3631702);engine=session.engine
        source=self.add(engine,'Clot Sliver',zone='hand',ref='ordinary-clot-source')
        recipient=self.add(engine,'Generic Ordinary Grant Sliver',seat='B',ref='ordinary-clot-recipient')
        non_sliver=self.add(engine,'Generic Bound Body',seat='B',ref='ordinary-clot-nonrecipient')
        destroy=self.add(engine,'Murder',zone='hand',ref='ordinary-clot-destroy')
        session.state.players['B'].mana_pool['C']=2
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':3,'B':3})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertFalse(any('Regenerate' in ability.effect_text for ability in engine._activated_abilities(non_sliver)))
        while session.state.priority_player!='B':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        ability=next(row for row in actions if row['id'].startswith('activate:'+recipient.ref+':'))
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':ability['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:B',{'action_id':ability['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(1,session.state.cards[recipient.object_id].regeneration_shields)
        self.assertEqual(0,session.state.cards[source.object_id].regeneration_shields)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(row.get('card')==destroy.ref for row in actions),
            {'pool':dict(session.state.players['A'].mana_pool),'zone':session.state.cards[destroy.object_id].zone,
             'phase':session.state.phase,'step':session.state.step,'active':session.state.active_player,
             'actions':[(row['id'],row.get('card')) for row in actions]})
        cast=next(row for row in actions if row.get('card')==destroy.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        current=session.state.cards[recipient.object_id]
        self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped)
        self.assertEqual(0,current.regeneration_shields)
        self.replay(session)

    def test_original_infernal_scarring_granted_death_draw_uses_recipient_controller_and_replays(self):
        session=self.session(3631703);engine=session.engine
        aura=self.add(engine,'Infernal Scarring',zone='hand',ref='ordinary-infernal-aura')
        recipient=self.add(engine,'Generic Bound Body',seat='B',ref='ordinary-infernal-recipient')
        destroy=self.add(engine,'Murder',zone='hand',ref='ordinary-infernal-destroy')
        self.assert_whole_admission(aura.printed_name)
        offered=self.ready(session,aura,{'C':2,'B':3})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(4,engine._numeric_stat(recipient.object_id,'power'))
        before_b=len(session.state.players['B'].zones['hand'])
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==destroy.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[recipient.object_id].zone)
        self.assertEqual(before_b+1,len(session.state.players['B'].zones['hand']))
        for observer in 'ACD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['B'])
        self.replay(session)
