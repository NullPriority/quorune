from __future__ import annotations

import unittest
from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_original_copperhoof_and_tapped_quantity_draw_track_actual_tap_actions_replay(self):
        session=self.session(3633301);engine=session.engine
        source=self.add(engine,'Copperhoof Vorrac',ref='tapped-quantity-vorrac')
        foreign=self.add(engine,'Generic Boolean Green Source',seat='B',ref='tapped-quantity-foreign-land')
        own=self.add(engine,'Generic Boolean Green Source',ref='tapped-quantity-own-land')
        self.assert_whole_admission(source.printed_name)
        setup=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='tapped-quantity-setup')
        self.ready(session,setup,{})
        self.checkpoint(session)
        initial=engine._numeric_stat(source.object_id,'power')
        while session.state.priority_player!='B':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        activate=next(row for row in actions if row['id'].startswith('activate:'+foreign.ref+':'))
        accepted=session.act('pilot:B',{'action_id':activate['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(initial-1,engine._numeric_stat(source.object_id,'power'))
        while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activate=next(row for row in actions if row['id'].startswith('activate:'+own.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':activate['id']}).ok)
        self.assertEqual(initial-1,engine._numeric_stat(source.object_id,'power'))
        self.replay(session,load=True);self.replay(session)
        session=self.session(3633302);engine=session.engine
        mana=self.add(engine,'Heritage Druid',ref='tapped-quantity-own-creature')
        elves=[self.add(engine,'Generic Selected Tap Elf',ref='tapped-quantity-elf-'+str(i)) for i in range(2)]
        self.add(engine,'Generic Bound Body',ref='tapped-quantity-untapped-control')
        draw=self.add(engine,'Generic Tapped Quantity Draw',zone='hand',ref='tapped-quantity-draw')
        self.assert_whole_admission(draw.printed_name)
        offered=self.ready(session,mana,{})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[mana.ref,*[elf.ref for elf in elves]]})
        self.assertTrue(accepted.ok,accepted.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==draw.ref)
        hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(hand+2,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_vivid_land_compound_entry_counter_price_and_actual_untap_replay(self):
        session=self.session(3633201);engine=session.engine
        land=self.add(engine,'Vivid Creek',zone='hand',ref='compound-vivid-land')
        spell=self.add(engine,'Essence Harvest',zone='hand',ref='compound-vivid-spell')
        self.assert_whole_admission(land.printed_name)
        setup=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='compound-vivid-setup')
        self.ready(session,setup,{'C':2})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('play-land:') and row.get('card')==land.ref)
        self.checkpoint(session);before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id']}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id']});self.assertTrue(accepted.ok,accepted.summary)
        current=session.state.cards[land.object_id]
        self.assertTrue(current.tapped);self.assertEqual(2,current.counters.get('charge',0))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+land.ref+':') for row in actions))
        self.replay(session,load=True)
        for _ in range(200):
            if session.state.active_player=='A' and not session.state.cards[land.object_id].tapped:break
            decision=session.state.pending_decision
            if decision.kind=='priority':command={'action_id':'pass'}
            elif decision.kind=='combat.attackers':command={'action_id':'attack','attackers':{}}
            elif decision.kind=='cleanup.discard':
                cards=[session.state.cards[oid].ref for oid in session.state.players[decision.actors[0]].zones['hand'][:decision.payload_by_actor[decision.actors[0]]['count']]]
                command={'action_id':'discard','cards':cards}
            else:self.fail('UnexpectednativeVividturn '+decision.kind)
            accepted=session.act(session.pending_principals()[0],command);self.assertTrue(accepted.ok,accepted.summary)
        else:self.fail('Vividlandnativeuntapdidnotoccur')
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        ability=next(ability for ability in engine._activated_abilities(session.state.cards[land.object_id]) if ability.source_counter_removal_cost is not None)
        offered=next(row for row in actions if row['id']=='activate:'+land.ref+':'+ability.ability_id)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'B':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[land.object_id].tapped);self.assertEqual(1,session.state.cards[land.object_id].counters.get('charge',0))
        self.assertEqual(1,session.state.players['A'].mana_pool['B'])
        self.replay(session,load=True);self.replay(session)

    def test_original_heritage_and_dryad_selected_tap_mana_atomic_costs_and_replay(self):
        session=self.session(3633101);engine=session.engine
        source=self.add(engine,'Heritage Druid',zone='hand',ref='tap-mana-heritage')
        bodies=[self.add(engine,'Generic Selected Tap Elf',ref='tap-mana-elf-'+str(i)) for i in range(2)]
        foreign=self.add(engine,'Generic Selected Tap Elf',seat='B',ref='tap-mana-foreign-elf')
        green_spell=self.add(engine,'Generic Conditional Green Qualifier',zone='hand',ref='tap-mana-pay-green')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'G':1})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        before=session.state.to_dict()
        for refs in ([source.ref,bodies[0].ref,bodies[0].ref],[source.ref,bodies[0].ref,foreign.ref],[source.ref,bodies[0].ref]):
            self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'cost_cards':refs}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[source.ref,*[body.ref for body in bodies]]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertFalse(session.state.stack)
        self.assertEqual(3,session.state.players['A'].mana_pool['G'])
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (source,*bodies)))
        self.assertFalse(session.state.cards[foreign.object_id].tapped)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==green_spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session);self.replay(session)
        session=self.session(3633102);engine=session.engine
        source=self.add(engine,'Loam Dryad',ref='tap-mana-dryad')
        body=self.add(engine,'Generic Selected Tap Elf',ref='tap-mana-fresh-selected')
        fresh_source=self.add(engine,'Loam Dryad',ref='tap-mana-fresh-source')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{})
        fresh_source.acquired_control_turn_count=session.state.players['A'].turns_begun
        engine.permissions.invalidate_current();session.state.pending_decision=None;engine._grant_priority('A');engine.pump()
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+fresh_source.ref+':') for row in actions))
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        self.checkpoint(session);before=session.state.to_dict()
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[source.ref],'mana_output':{'G':1}}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[body.ref],'mana_output':{'G':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped);self.assertTrue(session.state.cards[body.object_id].tapped)
        self.assertEqual(1,session.state.players['A'].mana_pool['G']);self.assertFalse(session.state.stack)
        self.replay(session,load=True)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])

    def test_original_phyrexian_altar_selected_sacrifice_mana_pays_real_spell_and_replays(self):
        session=self.session(3633001);engine=session.engine
        source=self.add(engine,'Phyrexian Altar',zone='hand',ref='selected-mana-altar')
        fodder=self.add(engine,'Generic Bound Body',ref='selected-mana-own-body')
        foreign=self.add(engine,'Generic Bound Body',seat='B',ref='selected-mana-foreign-body')
        spell=self.add(engine,'Essence Harvest',zone='hand',ref='selected-mana-life-spell')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':5})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        before=session.state.to_dict()
        for principal,cost in (('pilot:C',fodder.ref),('pilot:A',foreign.ref),('pilot:A',source.ref)):
            denied=session.act(principal,{'action_id':offered['id'],'cost_cards':[cost],'mana_output':{'B':1}})
            self.assertFalse(denied.ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[fodder.ref],'mana_output':{'B':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',session.state.cards[fodder.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[foreign.object_id].zone)
        self.assertEqual(1,session.state.players['A'].mana_pool['B']);self.assertFalse(session.state.stack)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(0,session.state.players['A'].mana_pool.get('B',0))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)
