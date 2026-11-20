"""Pinned Children/Sigil rulings: actual loss, all players, current resolution."""
from dataclasses import FrozenInstanceError,replace
import json,unittest
from types import SimpleNamespace as Row
from unittest.mock import patch
from quorune.model import TurnHistory,TurnHistoryEvent
from quorune.turn_history import reset_turn_history
from quorune.turn_life_loss_amount_model import AllPlayerTurnLifeLossSpec as Total
from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
from quorune.rules.turn_life_loss_recovery import resolve_all_player_turn_life_loss
from quorune.compiler.turn_life_loss_recovery import turn_life_loss_recovery_template
from quorune.oracle_ir import _effect_template


def history_host():
    events=[TurnHistoryEvent('player_lost_life',target='A',amount=5),
        TurnHistoryEvent('player_gained_life',target='A',amount=3),
        TurnHistoryEvent('player_lost_life',target='A',amount=2),
        TurnHistoryEvent('player_lost_life',target='B',amount=4),
        TurnHistoryEvent('player_lost_life',target='departed',amount=7)]
    return Row(state=Row(turn_sequence=9,turn_history=TurnHistory(turn_sequence=9,events=events),
        players={'A':Row(life=36),'B':Row(life=36)}))


class TurnLifeLossAmountTests(unittest.TestCase):
    def test_explicit_global_codec_is_immutable_closed_and_not_an_old_scalar(self):
        spec=Total();self.assertEqual(spec,Total.from_dict(json.loads(json.dumps(spec.to_dict()))))
        with self.assertRaises(FrozenInstanceError):spec.schema_version=2
        for change in ({'schema_version':True},{'schema_version':2},{'controller':'A'},{'history_fact':'life_gained'},{'scope':'controller'}):
            with self.assertRaises(ValueError):Total.from_dict({**spec.to_dict(),**change})
        with self.assertRaises(ValueError):ScalarEffectAmountSpec.from_dict(spec.to_dict())
        old=ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='life_lost')
        self.assertEqual(old,ScalarEffectAmountSpec.from_dict(old.to_dict()))
        with self.assertRaises(ValueError):Total.from_dict(old.to_dict())

    def test_total_uses_actual_losses_not_net_and_includes_departed_players(self):
        host=history_host();item=Row(controller='A',ref='stack',context={})
        before=tuple(host.state.turn_history.events)
        self.assertEqual(18,resolve_all_player_turn_life_loss(host,Total().to_dict(),item))
        controller=ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='life_lost')
        self.assertEqual(7,resolve_scalar_effect_amount(host,controller.to_dict(),item))
        item.controller='B';self.assertEqual(18,resolve_all_player_turn_life_loss(host,Total().to_dict(),item))
        self.assertEqual(4,resolve_scalar_effect_amount(host,controller.to_dict(),item))
        self.assertEqual(before,tuple(host.state.turn_history.events));self.assertEqual({},item.context)

    def test_late_loss_counts_once_empty_turn_is_zero_and_previous_turn_is_excluded(self):
        host=history_host();item=Row(controller='A')
        host.state.turn_history.events.append(TurnHistoryEvent('player_lost_life',target='B',amount=3))
        self.assertEqual(21,resolve_all_player_turn_life_loss(host,Total().to_dict(),item))
        host.state.turn_history=reset_turn_history(host.state.turn_history,10);host.state.turn_sequence=10
        self.assertEqual(0,resolve_all_player_turn_life_loss(host,Total().to_dict(),item))
        host.state.turn_sequence=11
        with self.assertRaises(ValueError):resolve_all_player_turn_life_loss(host,Total().to_dict(),item)
        host.state.turn_history=None
        with self.assertRaises(ValueError):resolve_all_player_turn_life_loss(host,Total().to_dict(),item)

    def test_malformed_loss_facts_do_not_become_a_gain_or_open_arithmetic(self):
        for amount in (-1,True):
            host=history_host();host.state.turn_history.events=[TurnHistoryEvent('player_lost_life',target='A',amount=amount)]
            with self.assertRaises(ValueError):resolve_all_player_turn_life_loss(host,Total().to_dict(),Row(controller='A'))

    def test_real_value_dispatch_keeps_controller_history_and_global_total_distinct(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        host=history_host();item=Row(controller='A',ref='stack',context={})
        self.assertEqual(18,resolve_semantic_value(host,Total().to_dict(),item))
        old=ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='life_lost')
        self.assertEqual(7,resolve_semantic_value(host,old.to_dict(),item))
        item.controller='B';self.assertEqual(18,resolve_semantic_value(host,Total().to_dict(),item))
        self.assertEqual(4,resolve_semantic_value(host,old.to_dict(),item))

    def test_fresh_controller_only_sum_mutant_is_killed_after_positive_all_player_total(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        host=history_host();item=Row(controller='A',ref='stack',context={})
        self.assertEqual(18,resolve_semantic_value(host,Total().to_dict(),item))
        def controller(host,value,item):
            return resolve_scalar_effect_amount(host,ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='life_lost').to_dict(),item)
        with patch('quorune.rules.turn_life_loss_recovery.resolve_all_player_turn_life_loss',side_effect=controller):
            with self.assertRaises(AssertionError):self.assertEqual(18,resolve_semantic_value(host,Total().to_dict(),item))


class TurnLifeLossRecoveryCompilerTests(unittest.TestCase):
    def compile(self,text):
        return turn_life_loss_recovery_template(text,compile_fixed=lambda body:_effect_template(body,card_name='Recovery Source'))

    def test_complete_printed_controller_and_all_player_clauses_keep_existing_one_gain(self):
        controller=self.compile("You gain life equal to the life you've lost this turn. (Damage causes loss of life.)")
        total=self.compile('You gain life equal to the total life lost by all players this turn. (Damage causes loss of life.)')
        for compiled in (controller,total):
            self.assertIsNotNone(compiled);self.assertIsNone(compiled[2]);self.assertEqual(1,len(compiled[1]))
            self.assertEqual(('life','$controller'),(compiled[1][0]['op'],compiled[1][0]['player']))
        self.assertEqual('life_lost',ScalarEffectAmountSpec.from_dict(controller[1][0]['delta']).history_fact)
        self.assertEqual(Total(),Total.from_dict(total[1][0]['delta']))

    def test_net_previous_turn_other_subjects_and_material_riders_remain_unrepresented(self):
        base='You gain life equal to the total life lost by all players this turn.'
        for bad in (base.replace('life lost','net life lost'),base.replace('this turn','last turn'),
                base.replace('all players','target player'),base+' Draw a card.',
                base.replace('life lost','cards discarded'),base+' (Invent an unrepresented ability.)'):
            self.assertIsNone(self.compile(bad),bad)

    def test_raw_value_result_and_resolution_frame_infer_ownership_without_coverage(self):
        from quorune.rules.turn_life_loss_recovery import turn_life_loss_program_dependencies,is_closed_turn_life_loss_program
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.turn_life_loss_amount_model import TURN_LIFE_LOSS_TOTAL_CAPABILITY as CAP
        compiled=self.compile('You gain life equal to the total life lost by all players this turn.')
        base={'event':'activate','active_zone':'battlefield','ability_id':'activation:front:n1',
            'effects':compiled[1],'handlers':(),'cost_schema':None,'target_schema':None,'coverage':()}
        program=Row(**base);required=turn_life_loss_program_dependencies(program)
        self.assertIn(CAP,required);self.assertIn('life.change.effect',required)
        self.assertIn(CAP,typed_result_amount_runtime_dependencies(program.effects))
        program.capability_dependencies=required;self.assertTrue(is_closed_turn_life_loss_program(program))
        program.capability_dependencies=tuple(c for c in required if c!=CAP);self.assertFalse(is_closed_turn_life_loss_program(program))
        for change in ({'event':'characteristics.evaluate'},{'target_schema':{'count':1}},
                {'effects':(*compiled[1],{'op':'draw','player':'$controller','count':1})}):
            with self.assertRaises(ValueError):turn_life_loss_program_dependencies(Row(**{**base,**change}))


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.turn_life_loss_amount_model import TURN_LIFE_LOSS_TOTAL_CAPABILITY as CAP
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

ORIGINALS=('Children of Korlis','Tainted Sigil')
FIXTURE=ROOT/'tests/fixtures/turn-life-loss-recovery-cards.json'


class TurnLifeLossRecoveryNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,FIXTURE],folder/'recovery.sqlite3')
        cls.db=CardDatabase(folder/'recovery.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Actual turn loss recovery',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def start(self,seed):
        s=self.session(seed)
        while len(s.state.players['A'].zones['hand'])>1:s.engine.move_card(s.state.players['A'].zones['hand'][-1],'library',log=False)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='unrelated-private-recovery')
        return s,private

    def activate_recovery(self,s,source,seat='A'):
        self.priority(s,seat);current=s.state.cards[source.object_id]
        action=next(a for a in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:'+current.ref+':'))
        command={'action_id':action['id'],'pay':'auto'};self.reject(s,'D' if seat!='D' else 'C',command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)

    def loss_fixture(self,name,seed):
        s,private=self.start(seed);source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
        own_bolt=self.add(s.engine,'Lightning Bolt',zone='hand',ref='own-loss-three')
        shock=self.add(s.engine,'Shock',zone='hand',ref='own-loss-two')
        other_bolt=self.add(s.engine,'Lightning Bolt',zone='hand',ref='other-loss-three')
        nectar=self.add(s.engine,'Sacred Nectar',zone='hand')
        for card in (own_bolt,shock,other_bolt,nectar):self.assert_closed(s,card)
        self.prepare(s,mana={'C':8,'W':6,'B':3,'R':5});self.checkpoint(s)
        return s,private,source,(own_bolt,shock,other_bolt,nectar)

    def actual_loss_history(self,s,cards):
        own_bolt,shock,other_bolt,nectar=cards
        for card,target in ((own_bolt,'A'),(shock,'A'),(other_bolt,'B')):
            self.cast(s,'A',s.state.cards[card.object_id],[target]);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',s.state.cards[nectar.object_id]);self.assertIsNone(self.resolve(s))
        self.assertEqual((39,37),(s.state.players['A'].life,s.state.players['B'].life))

    def test_complete_originals_admit_normally_and_global_value_ownership_cannot_be_removed(self):
        s,private=self.start(720000)
        for name in ORIGINALS:
            source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
            self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review').status)
        sigil=self.db.lookup('Tainted Sigil')
        program=next(p for p in s.engine.semantics.programs_for_oracle(sigil.oracle_id) if CAP in p.capability_dependencies)
        for cap in (CAP,'quantity_expression.scalar_effect_amount','life.change.effect'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            raw=replace(program,coverage=(),capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(raw,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_children_printed_cast_and_sacrifice_gain_controller_actual_loss_not_net(self):
        s,private,source,cards=self.loss_fixture('Children of Korlis',720010)
        before=dict(s.state.players['A'].mana_pool);self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual(before['W']-1,s.state.players['A'].mana_pool['W'])
        self.actual_loss_history(s,cards);before_gain=sum(e.kind=='player_gained_life' for e in s.state.turn_history.events)
        self.activate_recovery(s,source);self.assertIsNone(self.resolve(s))
        self.assertEqual((44,37,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
        gains=[e.amount for e in s.state.turn_history.events if e.kind=='player_gained_life']
        self.assertEqual(before_gain+1,len(gains));self.assertEqual(5,gains[-1]);self.finish(s,private)

    def test_actual_sigil_printed_cast_tap_sacrifice_includes_loss_before_source_entered(self):
        s,private,source,cards=self.loss_fixture('Tainted Sigil',720011)
        self.actual_loss_history(s,cards);before=dict(s.state.players['A'].mana_pool)
        self.cast(s,'A',s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s))
        self.assertEqual(before['W']-1,s.state.players['A'].mana_pool['W']);self.assertEqual(before['B']-1,s.state.players['A'].mana_pool['B'])
        self.activate_recovery(s,source);self.assertIsNone(self.resolve(s))
        self.assertEqual((47,37,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertEqual(8,[e.amount for e in s.state.turn_history.events if e.kind=='player_gained_life'][-1]);self.finish(s,private)

    def test_actual_zero_history_still_offers_and_pays_both_activation_prices(self):
        for index,name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                s,private=self.start(720020+index);source=self.add(s.engine,name,zone='hand');self.assert_closed(s,source)
                self.prepare(s,mana={'C':1,'W':1,'B':1});self.checkpoint(s);self.cast(s,'A',source)
                self.assertIsNone(self.resolve(s));self.activate_recovery(s,source);self.assertIsNone(self.resolve(s))
                self.assertEqual([40]*4,[s.state.players[p].life for p in 'ABCD'])
                self.assertEqual([], [e for e in s.state.turn_history.events if e.kind=='player_gained_life']);self.finish(s,private)

    def test_actual_tapped_source_excludes_sigil_price_but_children_can_still_sacrifice(self):
        for index,name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                s,private=self.start(720030+index);source=self.add(s.engine,name);self.assert_closed(s,source)
                self.prepare(s);action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if a['id'].startswith('activate:'+source.ref+':'))
                source.tapped=True;self.prepare(s);self.checkpoint(s)
                offered=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                command={'action_id':action['id'],'pay':'auto'}
                if name=='Tainted Sigil':
                    self.assertNotIn(action['id'],{a['id'] for a in offered});self.reject(s,'A',command)
                    self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                else:
                    self.assertIn(action['id'],{a['id'] for a in offered})
                    result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.finish(s,private)

    def test_actual_loss_response_after_source_sacrifice_is_included_at_resolution(self):
        s,private,source,cards=self.loss_fixture('Tainted Sigil',720040)
        response=self.add(s.engine,'Shock',zone='hand',ref='late-loss-response');self.assert_closed(s,response)
        self.checkpoint(s);self.actual_loss_history(s,cards);self.cast(s,'A',s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s))
        self.activate_recovery(s,source);self.cast(s,'A',s.state.cards[response.object_id],['B'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((49,35,40,40),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_paid_life_and_direct_losses_both_count_in_recorded_total(self):
        for index,name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                s,private=self.start(720041+index);source=self.add(s.engine,name)
                wall=self.add(s.engine,'Wall of Blood');spell=self.add(s.engine,'Stronghold Discipline',zone='hand')
                self.add(s.engine,'Grizzly Bears',seat='B')
                for card in (source,wall,spell):self.assert_closed(s,card)
                self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',spell);self.assertIsNone(self.resolve(s))
                for _ in range(2):
                    self.priority(s,'A');action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                        if a['id'].startswith('activate:'+s.state.cards[wall.object_id].ref+':'))
                    before=s.state.players['A'].life;result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                    self.assertTrue(result.ok,result.summary);self.assertEqual(before-1,s.state.players['A'].life);self.assertIsNone(self.resolve(s))
                self.activate_recovery(s,source);self.assertIsNone(self.resolve(s))
                self.assertEqual((40 if index==0 else 41,39,40,40),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_copied_recovery_uses_new_controller_history_or_global_total(self):
        for index,name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                s,private=self.start(720043+index);original=self.add(s.engine,name,seat='A')
                copy=self.add(s.engine,'Clever Impersonator',seat='B',zone='hand')
                bolt=self.add(s.engine,'Lightning Bolt',zone='hand');shock=self.add(s.engine,'Shock',seat='B',zone='hand')
                for card in (original,copy,bolt,shock):self.assert_closed(s,card)
                s.state.players['A'].mana_pool.update({'R':1})
                self.prepare(s,actor='B',active='B',mana={'C':2,'U':2,'R':1});self.checkpoint(s)
                self.cast(s,'A',bolt,['A']);self.assertIsNone(self.resolve(s));self.cast(s,'B',shock,['B']);self.assertIsNone(self.resolve(s))
                self.cast(s,'B',copy);self.assertEqual('replacement.order',self.resolve(s).kind);s=self.replay(s,load=True)
                option=next(o['id'] for o in s.packet('pilot:B',full=True)['decision']['ctx']['options']
                    if original.ref in o['id'] and not o['id'].startswith('decline:'))
                result=s.act('pilot:B',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.activate_recovery(s,copy,'B');self.assertIsNone(self.resolve(s))
                self.assertEqual((37,40 if index==0 else 43,40,40),tuple(s.state.players[p].life for p in 'ABCD'));self.finish(s,private)

    def test_actual_gain_multiplier_does_not_change_the_life_loss_total(self):
        s,private,source,cards=self.loss_fixture('Tainted Sigil',720045)
        reflection=self.add(s.engine,'Boon Reflection');self.assert_closed(s,reflection);self.checkpoint(s)
        own,shock,other,nectar=cards
        for card,target in ((own,'A'),(shock,'A'),(other,'B')):
            self.cast(s,'A',s.state.cards[card.object_id],[target]);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',s.state.cards[nectar.object_id]);self.assertIsNone(self.resolve(s));self.assertEqual(43,s.state.players['A'].life)
        self.cast(s,'A',s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s));self.activate_recovery(s,source)
        self.assertIsNone(self.resolve(s));self.assertEqual((59,37,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertEqual(16,[e.amount for e in s.state.turn_history.events if e.kind=='player_gained_life'][-1]);self.finish(s,private)

    def test_actual_prohibited_gain_still_offers_and_pays_the_source_cost(self):
        s,private=self.start(720046);source=self.add(s.engine,'Children of Korlis')
        knight=self.add(s.engine,"Knight of Dusk's Shadow",seat='B');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        for card in (source,knight,bolt):self.assert_closed(s,card)
        self.prepare(s,mana={'R':1});self.checkpoint(s);self.cast(s,'A',bolt,['A']);self.assertIsNone(self.resolve(s))
        self.activate_recovery(s,source);self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['A'].life)
        self.assertEqual([], [e for e in s.state.turn_history.events if e.kind=='player_gained_life']);self.finish(s,private)

    def test_actual_countered_ability_keeps_sacrificed_source_and_does_not_gain(self):
        s,private=self.start(720047);source=self.add(s.engine,'Children of Korlis')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand');counter=self.add(s.engine,'Stifle',seat='B',zone='hand')
        for card in (source,bolt,counter):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'U':1});self.prepare(s,mana={'R':1});self.checkpoint(s)
        self.cast(s,'A',bolt,['A']);self.assertIsNone(self.resolve(s));self.activate_recovery(s,source)
        self.cast(s,'B',counter,[s.state.stack[-1].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(37,s.state.players['A'].life);self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_next_turn_excludes_prior_loss_without_rewriting_old_history(self):
        s,private=self.start(720048);source=self.add(s.engine,'Tainted Sigil');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,bolt);self.prepare(s,mana={'R':1});self.checkpoint(s)
        self.cast(s,'A',bolt,['A']);self.assertIsNone(self.resolve(s));turn=s.state.turn_sequence
        self.until(s,lambda game:game.state.turn_sequence>turn)
        self.activate_recovery(s,source);self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['A'].life);self.finish(s,private)

    def test_actual_post_gain_fault_rolls_back_total_and_retries_without_refunding_cost(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        import quorune.effect_runtime.life_effects as owner
        s,private=self.start(720049);source=self.add(s.engine,'Children of Korlis');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,bolt);self.prepare(s,mana={'R':1});self.checkpoint(s)
        self.cast(s,'A',bolt,['A']);self.assertIsNone(self.resolve(s));self.activate_recovery(s,source)
        original=owner.commit_life_change_batch;observed=[];failed=None
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append(s.state.players['A'].life);raise GameRuleError('Injected actual recovery gain failure')
        with patch.object(owner,'commit_life_change_batch',side_effect=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([40],observed);self.assertIsNotNone(failed);self.assertEqual(37,s.state.players['A'].life)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);s=self.replay(s,load=True)
        result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['A'].life);self.finish(s,private)

    def test_fresh_native_controller_only_total_mutant_is_killed_after_real_sigil_action(self):
        observed=[]
        def controller(host,value,item):
            observed.append(item.controller)
            return resolve_scalar_effect_amount(host,ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='life_lost').to_dict(),item)
        with patch('quorune.rules.turn_life_loss_recovery.resolve_all_player_turn_life_loss',side_effect=controller):
            with self.assertRaises(AssertionError):self.test_actual_sigil_printed_cast_tap_sacrifice_includes_loss_before_source_entered()
        self.assertEqual(['A'],observed)

    def test_genuinely_verified_global_sum_withdrawal_keeps_controller_recovery_and_fixed_gain(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_bound_effect_programs import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['Global actual loss total withdrawn'])
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(self.db.lookup('Tainted Sigil'),capability_registry=registry,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(self.db.lookup('Children of Korlis'),capability_registry=registry,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(record('You gain 3 life.'),capability_registry=registry,capability_profile='commander_review').status)
