from __future__ import annotations

import unittest
from quorune.continuous_effects import Layer
from quorune.object_predicate import ObjectQuerySpec
from public_state_action_support import PublicStateActionFixture


class AdditionalConditionalModifierRuntimeTests(PublicStateActionFixture, unittest.TestCase):
    def test_original_ghalta_cost_uses_current_aggregate_after_actual_bounce_and_replays(self):
        session=self.session(3631602);engine=session.engine
        spell=self.add(engine,'Ghalta, Primal Hunger',zone='hand',ref='aggregate-ghalta')
        body=self.add(engine,'Generic Aggregate Life Body',ref='aggregate-cost-body')
        self.add(engine,'Generic Aggregate Foreign Body',seat='B',ref='aggregate-cost-foreign')
        bounce=self.add(engine,'Unsummon',zone='hand',ref='aggregate-cost-bounce')
        mana=self.add(engine,'Generic Aggregate Mana Source',ref='aggregate-cost-mana')
        self.assert_whole_admission(spell.printed_name)
        self.assert_whole_admission(mana.printed_name)
        self.ready(session,bounce,{'U':1,'G':2})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        stale=next(row for row in actions if row.get('card')==spell.ref)
        self.checkpoint(session)
        action=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==spell.ref for row in actions))
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:A',{'action_id':stale['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        cast=next(row for row in actions if row.get('card')==body.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.assertEqual(12,engine._numeric_stat(spell.object_id,'power'))
        self.assertEqual(0,session.state.players['A'].mana_pool.get('G',0))
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_constructed_aggregate_mana_pays_real_spell_with_current_stats_and_replays(self):
        """Complete constructed carrier; no claim about Selvala's other ability."""
        session=self.session(3631603);engine=session.engine
        mana=self.add(engine,'Generic Aggregate Mana Source',ref='aggregate-mana-source')
        body=self.add(engine,'Generic Aggregate Life Body',ref='aggregate-mana-body')
        self.add(engine,'Generic Aggregate Foreign Body',seat='C',ref='aggregate-mana-foreign')
        spell=self.add(engine,'Essence Harvest',zone='hand',ref='aggregate-mana-essence')
        self.assert_whole_admission(mana.printed_name)
        offered=self.ready(session,mana,{})
        self.assertEqual(10,offered['choice_schema']['mana_output']['total'])
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'mana_output':{'B':1,'G':9}}).ok)
        self.assertEqual(before,session.state.to_dict())
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'G':20}}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'B':1,'G':9}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[mana.object_id].tapped)
        self.assertEqual(9,session.state.players['A'].mana_pool['G'])
        self.assertEqual(1,session.state.players['A'].mana_pool['B'])
        self.assertFalse(session.state.stack)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(30,session.state.players['B'].life)
        self.assertEqual(50,session.state.players['A'].life)
        self.assertEqual(10,engine._numeric_stat(body.object_id,'power'))
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_essence_harvest_aggregate_resolves_once_across_life_changes_and_replays(self):
        session=self.session(3631601);engine=session.engine
        source=self.add(engine,'Essence Harvest',zone='hand',ref='aggregate-essence-harvest')
        creature=self.add(engine,'Generic Aggregate Life Body',ref='aggregate-life-sensitive-body')
        self.add(engine,'Generic Aggregate Foreign Body',seat='B',ref='aggregate-foreign-body')
        session.state.players['A'].life=35
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':2,'B':1})
        self.assertEqual(10,engine._numeric_stat(creature.object_id,'power'))
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'targets':['A'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(35,session.state.players['A'].life)
        self.assertEqual(10,engine._numeric_stat(creature.object_id,'power'))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_tyranid_opponent_quantity_resolves_after_actual_concession_and_replays(self):
        session=self.session(3631401);engine=session.engine
        spell=self.add(engine,'Tyranid Invasion',zone='hand',ref='public-opponent-tokens')
        self.assert_whole_admission(spell.printed_name)
        action=self.ready(session,spell,{'C':3,'G':1})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        while session.state.priority_player!='C':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.replay(session,load=True)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':'concede'}).ok)
        self.assertEqual(before,session.state.to_dict())
        conceded=session.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(conceded.ok,conceded.summary)
        self.assertFalse(session.state.players['C'].in_game)
        self.resolve(session)
        tokens=[card for card in session.state.cards.values() if card.controller=='A' and card.zone=='battlefield' and card.object_kind=='token']
        self.assertEqual(2,len(tokens))
        for token in tokens:
            self.assertEqual(3,engine._numeric_stat(token.object_id,'power'))
            self.assertEqual(3,engine._numeric_stat(token.object_id,'toughness'))
            self.assertIn('Trample',engine._effective_card_data(token)['keywords'])
        for seat in 'BD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_token_doubler_owner_diagnostic_respects_controller_and_current_ability_removal(self):
        """Engine-owner contrast; the Master witness supplies native command evidence."""
        from quorune.continuous_effects import ContinuousEffect,ContinuousEffectOrigin,ContinuousEffectDuration,ContinuousObjectIdentity,ContinuousOperation
        from quorune.continuous_effect_state import commit_continuous_effect
        session=self.session(3631302);engine=session.engine
        source=self.add(engine,'Generic Counted Token Doubler',ref='token-doubler-removal-source')
        self.assert_whole_admission(source.printed_name)
        definition={'type_line':'Token Creature — Wolf','power':'1','toughness':'1','colors':[],'keywords':[]}
        self.assertEqual(1,len(engine.create_token('B',name='Foreign Owner Diagnostic',characteristics=definition)))
        self.assertEqual(2,len(engine.create_token('A',name='Own Owner Diagnostic',characteristics=definition)))
        commit_continuous_effect(engine.state,ContinuousEffect(effect_id='fixture:remove-token-doubler',source_id='fixture:ability-removal',
            layer=Layer.ABILITY,sublayer='6',timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation('remove_all_abilities'),),origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,applies=ObjectQuerySpec(zones=('battlefield',)),
            locked_objects=(ContinuousObjectIdentity(source.object_id,source.logical_object_id),)))
        self.assertEqual(1,len(engine.create_token('A',name='Removed Owner Diagnostic',characteristics=definition)))

    def test_original_master_waves_value_tokens_double_enter_with_anthem_and_die_after_bounce_replays(self):
        session=self.session(3631301);engine=session.engine
        master=self.add(engine,'Master of Waves',zone='hand',ref='counted-master-waves')
        self.add(engine,'Generic Devotion Amplifier',ref='counted-token-devotion-modifier')
        self.add(engine,'Generic Devotion Hybrid',ref='counted-token-devotion-hybrid')
        doubler=self.add(engine,'Parallel Lives',zone='hand',ref='counted-token-doubler')
        island=self.add(engine,'Generic Conditional Keyword Island',ref='counted-token-island')
        colorless=self.add(engine,'Generic Counted Token Three Mana',ref='counted-token-colorless')
        bounce=self.add(engine,'Generic Devotion Creature Bounce',zone='hand',ref='counted-master-bounce')
        self.assert_whole_admission(master.printed_name)
        self.assert_whole_admission('Parallel Lives')
        offered=self.ready(session,doubler,{'C':3,'G':1})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        for land in (island,colorless):
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            mana=next(row for row in actions if row['id'].startswith('activate:'+land.ref+':'))
            self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==master.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True)
        self.resolve(session)
        tokens=[card for card in session.state.cards.values() if card.zone=='battlefield' and card.controller=='A' and card.object_kind=='token']
        self.assertEqual(6,len(tokens))
        for token in tokens:
            self.assertEqual(2,engine._numeric_stat(token.object_id,'power'))
            self.assertEqual(1,engine._numeric_stat(token.object_id,'toughness'))
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[master.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('hand',session.state.cards[master.object_id].zone)
        self.assertFalse(any(token.object_id in session.state.players['A'].zones['battlefield'] for token in tokens))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_karametra_acolyte_mana_uses_devotion_and_current_modifier_with_replay(self):
        session=self.session(3631205);engine=session.engine
        source=self.add(engine,"Karametra's Acolyte",ref='devotion-mana-acolyte')
        self.add(engine,'Generic Devotion Amplifier',ref='devotion-mana-amplifier')
        self.add(engine,'Generic Devotion Green Hybrid',ref='devotion-mana-hybrid')
        self.add(engine,'Generic Devotion Green Hybrid',seat='B',ref='devotion-mana-foreign')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id']}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(3,session.state.players['A'].mana_pool['G'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session,load=True)

    def test_devotion_cost_modifier_changes_offer_and_accepted_cast_then_private_draw_replays(self):
        session=self.session(3631204);engine=session.engine
        source=self.add(engine,'Generic Devotion Cost Spell',zone='hand',ref='devotion-cost-spell')
        amplifier=self.add(engine,'Generic Devotion Amplifier',ref='devotion-cost-amplifier')
        self.add(engine,'Generic Devotion Hybrid',ref='devotion-cost-hybrid')
        bounce=self.add(engine,'Generic Devotion Creature Bounce',zone='hand',ref='devotion-cost-modifier-bounce')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':3,'U':1})
        self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':cast['id'],'targets':[amplifier.ref],'pay':'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==source.ref for row in actions))
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        recast=next(row for row in actions if row.get('card')==amplifier.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':recast['id'],'pay':'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==source.ref)
        before=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session,load=True)

    def test_original_daxos_devotion_cast_uses_live_modifier_ability_and_departure_with_replay(self):
        from quorune.devotion import current_devotion
        for remove_ability in (False,True):
            with self.subTest(remove_ability=remove_ability):
                session=self.session(3631201+int(remove_ability));engine=session.engine
                daxos=self.add(engine,'Daxos, Blessed by the Sun',zone='hand',ref='devotion-daxos')
                amplifier=self.add(engine,'Generic Devotion Amplifier',ref='devotion-amplifier')
                hybrid=self.add(engine,'Generic Devotion Hybrid',ref='devotion-hybrid')
                self.add(engine,'Generic Devotion Hybrid',seat='B',ref='devotion-foreign-hybrid')
                removal=self.add(engine,'Snakeform' if remove_ability else 'Generic Devotion Creature Bounce',zone='hand',ref='devotion-modifier-ending')
                island=self.add(engine,'Generic Conditional Keyword Island',ref='devotion-removal-island')
                bounce=self.add(engine,'Generic Devotion Artifact Bounce',zone='hand',ref='devotion-hybrid-bounce')
                self.assert_whole_admission(daxos.printed_name)
                offered=self.ready(session,daxos,{'W':4})
                self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual(4,current_devotion(engine,'A',('W',)))
                self.assertEqual(4,engine._numeric_stat(daxos.object_id,'toughness'))
                self.assertEqual(2,engine._numeric_stat(daxos.object_id,'power'))
                for spell,target,expected in ((removal,amplifier,3),(bounce,hybrid,2)):
                    while session.state.priority_player!='A':
                        result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                        self.assertTrue(result.ok,result.summary)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if remove_ability and spell is removal:
                        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
                        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
                        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==spell.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[target.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                    self.assertEqual(expected,current_devotion(engine,'A',('W',)))
                    self.assertEqual(expected,engine._numeric_stat(daxos.object_id,'toughness'))
                for seat in 'BCD':
                    self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session,load=remove_ability)

    def test_original_nylea_disciple_devotion_life_gain_resolves_after_actual_cast_and_replays(self):
        session=self.session(3631203);engine=session.engine
        source=self.add(engine,"Nylea's Disciple",zone='hand',ref='devotion-disciple')
        self.add(engine,'Generic Devotion Amplifier',ref='devotion-life-amplifier')
        self.add(engine,'Generic Devotion Green Hybrid',ref='devotion-green-hybrid')
        self.add(engine,'Generic Devotion Green Hybrid',seat='C',ref='devotion-foreign-green')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'G':2,'C':2})
        life=session.state.players['A'].life
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(life+4,session.state.players['A'].life)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)
