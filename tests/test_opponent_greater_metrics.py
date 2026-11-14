"""Current per-opponent comparisons shared by conditions and activation rules."""
import json,unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from quorune.player_maximum_model import PlayerMaximumSpec
from quorune.player_maximum import opponent_has_greater_metric,current_player_maximum
from quorune.compiler.player_maximum_templates import opponent_more_metric_condition
from quorune.continuous_conditions import FixedPublicStateConditionSpec
from quorune.object_query import ObjectQueryResult
import test_fixed_creature_power_damage as records
import test_recipient_control as recipients
import test_player_maximum_control as maximum_fixtures
from inherited_composition_fixture import InheritedNativeFixture
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry


class OpponentGreaterMetricModelCompilerTests(unittest.TestCase):
    def world(self,counts=(3,2,2,0)):
        players={seat:SimpleNamespace(in_game=True,life=value,zones={'hand':list(range(value))}) for seat,value in zip('ABCD',counts)}
        cards={}
        for seat,value in zip('ABCD',counts):
            for i in range(value):
                key=seat+str(i);cards[key]=SimpleNamespace(object_id=key,ref=key,owner='D',controller=seat,zone='battlefield',phased_out=False)
        return SimpleNamespace(players=players,turn_order=list('ABCD'),cards=cards)

    def row(self,card):
        return ObjectQueryResult(card.object_id,card.ref,'Land','D',card.controller,'battlefield',types=('land',))

    def test_comparison_is_per_opponent_not_sum_tie_or_ownership(self):
        spec=PlayerMaximumSpec('land_count')
        for counts,expected in (((3,2,2,0),False),((3,3,0,0),False),((3,4,0,0),True),((0,0,0,0),False)):
            with self.subTest(counts=counts):
                state=self.world(counts)
                self.assertEqual(expected,opponent_has_greater_metric(state,'A',spec,public_object_resolver=self.row))
        state=self.world((3,4,0,0));state.players['B'].in_game=False
        self.assertFalse(opponent_has_greater_metric(state,'A',spec,public_object_resolver=self.row))
        state=self.world((3,4,0,0));state.cards['B0'].phased_out=True
        self.assertFalse(opponent_has_greater_metric(state,'A',spec,public_object_resolver=self.row))

    def test_public_life_hand_and_legacy_maximum_remain_distinct(self):
        state=self.world((3,2,2,0))
        for metric in ('life','hand_count'):
            spec=PlayerMaximumSpec(metric)
            self.assertFalse(opponent_has_greater_metric(state,'A',spec));self.assertEqual('A',current_player_maximum(state,spec))
            self.assertTrue(opponent_has_greater_metric(state,'B',spec))
        with self.assertRaises(ValueError):opponent_has_greater_metric(state,'Z',PlayerMaximumSpec('life'))
        with self.assertRaises(ValueError):opponent_has_greater_metric(state,'A',PlayerMaximumSpec('land_count'))

    def test_closed_boolean_codec_and_comparison_grammar(self):
        for text,metric in (('an opponent controls more lands than you','land_count'),('an opponent has more life than you','life'),
                ('an opponent has more cards in hand than you','hand_count'),('an opponent controls more creatures than you','creature_count')):
            condition=opponent_more_metric_condition(text);self.assertEqual(metric,condition.fact_parameter)
            self.assertEqual(condition,FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
            for change in ({'amount':2},{'schema_version':True},{'fact_parameter':'unknown'}):
                with self.subTest(change=change),self.assertRaises(ValueError):FixedPublicStateConditionSpec.from_dict({**condition.to_dict(),**change})
        for text in ('opponents control more lands than you','an opponent controls as many lands as you','an opponent has more unknown things than you'):
            self.assertIsNone(opponent_more_metric_condition(text))
        text='When this creature enters, if an opponent controls more lands than you, create a Treasure token.'
        ir=compile_oracle_card(records.card_record(text,type_line='Creature — Scout'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)

    def test_fresh_comparison_witness_kills_summing_opponents(self):
        from quorune import player_maximum
        method='test_comparison_is_per_opponent_not_sum_tie_or_ownership';getattr(type(self)(method),method)()
        def wrong(state,actor,spec,**kwargs):
            values=dict(player_maximum.current_player_metric_values(state,spec,**kwargs))
            return sum(v for p,v in values.items() if p!=actor)>values[actor]
        with patch(__name__+'.opponent_has_greater_metric',wrong):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


class OpponentGreaterMetricNativeTests(InheritedNativeFixture,unittest.TestCase):
    setUpClass=classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene=recipients.RecipientControlNativeTests.scene
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish
    finish_turn=recipients.RecipientControlNativeTests.finish_turn

    def prepare(self,s,*,mana):
        if getattr(self,'late_setup',False):maximum_fixtures.PlayerMaximumNativeTests.prepare(self,s,mana=mana)
        else:InheritedNativeFixture.prepare(self,s,mana=mana)

    def board(self,name,*,opponent=4,zone='hand',late=False):
        self.late_setup=late
        extra={}
        def before(s,source,gift):
            extra['lands']={}
            for seat,count in (('A',3),('B',opponent),('C',2)):
                extra['lands'][seat]=[self.add(s.engine,'Island',seat=seat,ref='comparison-land-'+seat+str(i)) for i in range(count)]
            extra['bounce']=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
            extra['theft']=self.add(s.engine,'Turn Against',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'C':4,'R':1})
            extra['tundra']=self.add(s.engine,'Tundra',zone='library')
            extra['forest']=self.add(s.engine,'Forest',zone='library')
        s,source,gift,private=self.scene(name,zone=zone,before=before)
        return s,source,gift,private,extra

    def test_original_ticket_tortoise_compares_each_opponent_and_rechecks_after_response(self):
        for opponent,bounce in ((2,False),(4,False),(4,True)):
            with self.subTest(opponent=opponent,bounce=bounce):
                s,source,gift,private,extra=self.board('Ticket Tortoise',opponent=opponent)
                self.cast(s,'A',source);self.resolve_top(s)
                self.assertEqual(0 if opponent==2 else 1,self.trigger_count(s,source))
                if bounce:self.cast(s,'B',extra['bounce'],[extra['lands']['B'][0].ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
                self.assertEqual(1 if opponent==4 and not bounce else 0,len(tokens))
                if tokens:self.assertEqual(('A','A'),(tokens[0].owner,tokens[0].controller))
                self.finish(s,private)

    def search(self,s):
        for _ in range(6):
            decision=self.resolve(s)
            if decision is not None and decision.kind=='semantic.search':return s.packet('pilot:A',full=True)['decision']
            self.assertIsNotNone(decision)
            self.assertEqual('semantic.choice',decision.kind)
            command={'action_id':'choose','choice':'apply'};self.reject(s,'B',command)
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.fail('Library search did not become available')

    def select_search(self,s,refs):
        before=s.state.players['A'].stats.get('shuffle_count',0)
        command={'action_id':'choose','search_cards':list(refs)};self.reject(s,'B',command)
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(before+1,s.state.players['A'].stats['shuffle_count'])
        return s

    def test_four_original_plains_search_entries_preserve_basic_filters_and_tappedness(self):
        for name,basic,tapped in (('Scouting Hawk',True,True),('Loyal Warhound',True,True),
                ('Knight of the White Orchid',False,False),('Space Marine Scout',False,True)):
            with self.subTest(name=name):
                s,source,gift,private,extra=self.board(name)
                self.cast(s,'A',source);decision=self.search(s)
                refs={r['id'] for r in decision['ctx']['search_cards']}
                self.assertNotIn(extra['forest'].ref,refs)
                self.assertEqual(not basic,extra['tundra'].ref in refs)
                selected=next(ref for ref in refs if ref!=extra['tundra'].ref) if basic else extra['tundra'].ref
                for viewer in 'BCD':self.assertNotIn(selected,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.reject(s,'A',{'action_id':'choose','search_cards':[extra['forest'].ref]})
                s=self.select_search(s,[selected]);chosen=next(c for c in s.state.cards.values() if c.ref==selected)
                self.assertEqual(('battlefield','A',tapped),(chosen.zone,chosen.controller,chosen.tapped))
                self.finish(s,private)

    def test_gift_and_tax_search_different_three_card_sets_and_reveal_the_results(self):
        for name in ('Gift of Estates','Land Tax'):
            with self.subTest(name=name):
                tax=name=='Land Tax';s,source,gift,private,extra=self.board(name,zone='battlefield' if tax else 'hand',late=tax)
                if tax:self.finish_turn(s);self.assertEqual(('A','upkeep'),(s.state.active_player,s.state.step))
                else:self.cast(s,'A',source)
                decision=self.search(s);refs={r['id'] for r in decision['ctx']['search_cards']}
                self.assertEqual(tax,extra['forest'].ref in refs);self.assertEqual(not tax,extra['tundra'].ref in refs)
                special=extra['forest'].ref if tax else extra['tundra'].ref
                selected=[special,*sorted(refs-{special,extra['forest'].ref,extra['tundra'].ref})[:2]]
                s=self.select_search(s,selected)
                for ref in selected:
                    card=next(c for c in s.state.cards.values() if c.ref==ref);self.assertEqual(('hand','A'),(card.zone,card.owner))
                    self.assertIn(ref,json.dumps(s.packet('pilot:C',full=True)))
                self.finish(s,private)

    def test_wayfarer_offer_acceptance_and_accepted_search_use_their_correct_timing(self):
        for opponent in (2,4):
            with self.subTest(opponent=opponent):
                s,source,gift,private,extra=self.board('Weathered Wayfarer',opponent=opponent,zone='battlefield')
                self.priority(s,'A');rows=[r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':')]
                self.assertEqual(bool(rows),opponent==4)
                if not rows:
                    self.reject(s,'A',{'action_id':'activate:'+source.ref+':ab1','pay':'auto'});self.finish(s,private);continue
                command={'action_id':rows[0]['id'],'pay':'auto'};self.reject(s,'B',command)
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertTrue(s.state.cards[source.object_id].tapped)
                self.cast(s,'B',extra['bounce'],[extra['lands']['B'][0].ref]);self.resolve_top(s)
                self.search(s);s=self.select_search(s,[extra['forest'].ref])
                self.assertEqual('hand',s.state.cards[extra['forest'].object_id].zone);self.finish(s,private)

    def test_lander_can_be_used_and_its_land_entry_triggers_the_original_landfall(self):
        s,source,gift,private,extra=self.board('Sunstar Expansionist')
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        token,=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        power=s.engine._numeric_stat(source.object_id,'power');self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+token.ref+':'))
        command={'action_id':row['id'],'pay':'auto'};self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertNotIn(token.object_id,s.state.players['A'].zones['battlefield'])
        decision=self.search(s);chosen=decision['ctx']['search_cards'][0]['id']
        s=self.select_search(s,[chosen]);card=next(c for c in s.state.cards.values() if c.ref==chosen)
        self.assertEqual(('battlefield',True),(card.zone,card.tapped))
        self.assertEqual(power+1,s.engine._numeric_stat(source.object_id,'power'));self.finish(s,private)

    def test_source_control_change_keeps_comparison_and_token_creation_on_the_locked_actor(self):
        s,source,gift,private,extra=self.board('Ticket Tortoise')
        self.cast(s,'A',source);self.resolve_top(s)
        self.cast(s,'B',extra['theft'],[source.ref]);self.resolve_top(s)
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        token,=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(('A','A'),(token.owner,token.controller));self.finish(s,private)

    def test_comparison_authority_is_required_for_trigger_spell_and_activation_without_labels(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        cap='condition.player.opponent_greater_metric'
        for name in ('Ticket Tortoise','Gift of Estates','Weathered Wayfarer'):
            with self.subTest(name=name):
                s,source,gift,private,extra=self.board(name)
                program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies)
                deps=tuple(c for c in program.capability_dependencies if c!=cap)
                stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
