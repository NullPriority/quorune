"""CR702.59a/603.10a: fixed price, exact reminder, existing cost wire intact."""
from dataclasses import FrozenInstanceError
import json, unittest
from quorune.recover_model import RecoverSpec
from quorune.fixed_effect_payment import FixedEffectPaymentSpec
from quorune.compiler.recover_nodes import recover_spec_from_line
from types import SimpleNamespace as Row
from dataclasses import replace
from quorune.replacement.immutable import FrozenMap


class RecoverModelTests(unittest.TestCase):
    def test_fixed_price_is_immutable_and_roundtrips_without_reinterpreting_existing_payment(self):
        spec=recover_spec_from_line('Recover {2}{U}{U}')
        self.assertIsNotNone(spec);self.assertEqual(spec,RecoverSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        self.assertEqual(spec.payment,FixedEffectPaymentSpec.from_dict(spec.payment.to_dict()))
        with self.assertRaises(FrozenInstanceError):spec.schema_version=2
        with self.assertRaises(TypeError):spec.payment.requirements['U']=4

    def test_closed_cost_rejects_unknown_versions_fields_and_nonmana_interpretations(self):
        spec=recover_spec_from_line('Recover {1}{W}')
        for change in ({'schema_version':True},{'schema_version':2},{'active_zone':'battlefield'},{'cost':'{R}'}):
            with self.assertRaises(ValueError):RecoverSpec.from_dict({**spec.to_dict(),**change})
        with self.assertRaises(ValueError):RecoverSpec(FixedEffectPaymentSpec('life',amount=2))
        with self.assertRaises(ValueError):RecoverSpec.from_dict(spec.payment.to_dict())
        with self.assertRaises(ValueError):FixedEffectPaymentSpec.from_dict(spec.to_dict())


class RecoverCompilerTests(unittest.TestCase):
    def test_complete_spell_parent_keeps_recover_out_of_its_ordinary_resolution(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_bound_effect_programs import record
        card=replace(record('You gain 4 life.\nRecover {1}{W}','Sorcery'),keywords=('Recover',))
        ir=compile_oracle_card(card,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual([('resolve','stack'),('creature.dies','graveyard')],[(n.event,n.active_zone) for f in ir.faces for n in f.nodes])
        bad=replace(card,oracle_text='You gain 4 life.\nRecover—Pay half your life, rounded down.')
        self.assertNotEqual('exact',compile_oracle_card(bad,capability_registry=load_default_capability_registry(),capability_profile='commander_review').status)

    def test_all_six_pinned_fixed_prices_match_with_and_without_the_exact_reminder(self):
        for cost in ('{2}{U}{U}','{2}{B}','{R}{R}','{1}{B}{B}','{1}{G}','{1}{W}'):
            with self.subTest(cost=cost):
                reminder=('When a creature is put into your graveyard from the battlefield, you may pay '+cost+
                    '. If you do, return this card from your graveyard to your hand. Otherwise, exile this card.')
                simple=recover_spec_from_line('Recover '+cost)
                self.assertIsNotNone(simple);self.assertEqual(simple,recover_spec_from_line('Recover '+cost+' ('+reminder+')'))
                self.assertGreater(sum(simple.payment.requirements.values()),0)

    def test_other_prices_changed_timing_and_material_reminder_riders_stay_unrepresented(self):
        base=('Recover {1}{W} (When a creature is put into your graveyard from the battlefield, you may pay {1}{W}. '
            'If you do, return this card from your graveyard to your hand. Otherwise, exile this card.)')
        for line in ('Recover {X}','Recover {0}','Recover {W/U}','Recover {S}',
                'Recover—Pay half your life, rounded down.',base.replace('a creature','another creature'),
                base.replace('you may pay {1}{W}','you may pay {1}{B}'),base.replace('Otherwise, exile this card.',''),
                base+' Draw a card.',base.replace('from the battlefield','from anywhere')):
            self.assertIsNone(recover_spec_from_line(line),line)

    def test_keyword_node_uses_owner_graveyard_death_and_never_skips_missing_authority(self):
        from quorune.compiler.recover_nodes import recover_keyword_node
        from quorune.compiler.ir_model import SourceSpan
        line='Recover {2}{B}';residuals=[]
        node=recover_keyword_node(node_id='recover:one',line=line,material_line=line,
            span=SourceSpan(0,0,len(line)),capability_registry=None,capability_profile='commander_review',residuals=residuals)
        self.assertIsNotNone(node);self.assertFalse(node.exact);self.assertTrue(node.lowerable)
        self.assertEqual(('graveyard','creature.dies'),(node.active_zone,node.event))
        self.assertEqual({'field':'owner','op':'eq','value':'$source.owner'},node.event_condition)
        self.assertEqual(line,node.text);self.assertEqual(1,len(residuals))
        self.assertIsNone(recover_keyword_node(node_id='recover:bad',line=line+' (Unknown material rider.)',
            material_line=line,span=SourceSpan(0,0,len(line)+26),capability_registry=None,
            capability_profile='commander_review',residuals=[]))

    def test_raw_recover_frame_requires_every_owner_without_using_coverage(self):
        from quorune.rules.recover import recover_payment_effect,recover_program_dependencies,is_closed_recover_program
        effect=recover_payment_effect(recover_spec_from_line('Recover {1}{W}'))
        raw={'effects':(effect,),'event':'creature.dies','active_zone':'graveyard','cost_schema':None,
             'target_schema':None,'event_condition':{'field':'owner','op':'eq','value':'$source.owner'},'coverage':()}
        program=Row(**raw);required=recover_program_dependencies(program);program.capability_dependencies=required
        self.assertTrue(is_closed_recover_program(program))
        for missing in required:
            program.capability_dependencies=tuple(v for v in required if v!=missing);self.assertFalse(is_closed_recover_program(program))
        for change in ({'active_zone':'battlefield'},{'event':'permanent.enter'},
                {'event_condition':{'field':'controller','op':'eq','value':'$source.controller'}},
                {'effects':(effect,{'op':'draw','count':1,'player':'$controller'})},{'target_schema':{'count':1}}):
            with self.assertRaises(ValueError):recover_program_dependencies(Row(**{**raw,**change}))
        with self.assertRaises(ValueError):recover_program_dependencies(Row(**{**raw,'effects':(),'handlers':({'nested':effect},)}))


class RecoverPaymentAndResultTests(unittest.TestCase):
    def context(self,payable=True,actor='A'):
        from quorune.semantic_choices.context import SemanticChoiceContext
        query=Row(active_seats=('A','B'),cost_is_affordable=lambda seat,cost:payable)
        return SemanticChoiceContext(actor=actor,stack_ref='stack',stack_controller=actor,stack_label='Recover',
            source_ref='source',card_ref=None,semantic_program_id='recover',semantic_program_version=1,
            query=query,source_logical_object_id='object@1',source_object_id='object')

    def continuation(self,effect,actor='A'):
        from quorune.semantic_choices.model import SemanticChoiceFrame,SemanticChoiceContinuation
        frame=SemanticChoiceFrame(semantic_program_id='recover',semantic_program_version=1,
            stack_object='stack',instruction_pointer=0,controller=actor)
        return SemanticChoiceContinuation(handler_id='test.recover',handler_version=1,stack_ref='stack',
            effect=effect,remaining=(),destination=None,note='Recover',semantic_frame=frame)

    def test_prepare_and_complete_share_payment_authority_and_preserve_decline_result(self):
        from quorune.rules.recover import recover_payment_effect
        from quorune.semantic_choices.recover import prepare_recover,complete_recover
        from quorune.semantic_runtime.intents import PayManaCostIntent
        effect={**recover_payment_effect(recover_spec_from_line('Recover {1}{W}')),'player':'A'}
        context=self.context();prepared=prepare_recover(effect,context)
        self.assertEqual((True,False),prepared.request.choice.legal_values)
        yes=complete_recover(self.continuation(prepared.continuation_effect),{'pay':True},context.query)
        self.assertIsInstance(yes.intents[0],PayManaCostIntent);self.assertEqual('hand',yes.prepend_effects[0]['destination'])
        no=complete_recover(self.continuation(prepared.continuation_effect),{'pay':False},context.query)
        self.assertEqual((),no.intents);self.assertEqual('exile',no.prepend_effects[0]['destination'])
        unavailable=self.context(False);prepared=prepare_recover(effect,unavailable)
        self.assertEqual((False,),prepared.request.choice.legal_values)
        with self.assertRaises(ValueError):complete_recover(self.continuation(prepared.continuation_effect),{'pay':True},unavailable.query)

    def test_changed_frame_cost_actor_identity_and_response_fields_fail_closed(self):
        from quorune.rules.recover import recover_payment_effect
        from quorune.semantic_choices.recover import prepare_recover,complete_recover
        effect={**recover_payment_effect(recover_spec_from_line('Recover {R}{R}')),'player':'A'}
        context=self.context();prepared=prepare_recover(effect,context);cont=self.continuation(prepared.continuation_effect)
        for change in ({'_choice_actor':'B'},{'_recover_identity':''},{'schema_version':True},{'extra':1}):
            with self.assertRaises(ValueError):complete_recover(replace(cont,effect=FrozenMap({**dict(cont.effect),**change})),{'pay':False},context.query)
        for response in ({'pay':True,'player':'B'},{'pay':1},{'pay':True,'cards':['source']}):
            with self.assertRaises(ValueError):complete_recover(cont,response,context.query)
        with self.assertRaises(ValueError):prepare_recover(effect,replace(context,source_logical_object_id=None))

    def test_departed_source_does_not_remove_the_optional_payment_choice(self):
        from quorune.rules.recover import recover_payment_effect
        from quorune.semantic_choices.recover import prepare_recover,complete_recover
        effect={**recover_payment_effect(recover_spec_from_line('Recover {1}{G}')),'player':'A'}
        context=replace(self.context(),source_ref=None,source_object_id=None)
        prepared=prepare_recover(effect,context);self.assertEqual((True,False),prepared.request.choice.legal_values)
        result=complete_recover(self.continuation(prepared.continuation_effect),{'pay':True},context.query)
        self.assertEqual(1,len(result.intents));self.assertEqual('object@1',result.prepend_effects[0]['identity'])

    def test_result_uses_original_incarnation_after_cost_and_distinguishes_copied_controller_return(self):
        from quorune.effect_runtime.recover import apply_recover_source_result
        from quorune.rules.recover import recover_result_effect
        calls=[];card=Row(object_id='object',ref='source',logical_object_id='object@1',zone='graveyard',owner='A',is_card_object=True)
        host=Row(state=Row(cards={'object':card}),move_objects_simultaneously_intent=lambda intent:calls.append(intent) or (intent.destination,))
        def result(actor,paid):
            effect=recover_result_effect(actor=actor,identity='object@1',paid=paid)
            return {**effect,'card':'source','_runtime_source':{'object_id':'object','logical_object_id':'object@1'}}
        self.assertEqual(('hand',),apply_recover_source_result(host,result('A',True),'A'))
        self.assertEqual((),apply_recover_source_result(host,result('B',True),'B'))
        self.assertEqual(('exile',),apply_recover_source_result(host,result('B',False),'B'))
        card.logical_object_id='object@2'
        self.assertEqual((),apply_recover_source_result(host,result('A',False),'A'))
        card.logical_object_id='object@1';card.zone='exile'
        self.assertEqual((),apply_recover_source_result(host,result('A',True),'A'));self.assertEqual(2,len(calls))

    def test_fresh_decline_branch_mutant_is_killed_after_positive_original_source_return(self):
        from unittest.mock import patch
        import quorune.effect_runtime.recover as owner
        self.test_result_uses_original_incarnation_after_cost_and_distinguishes_copied_controller_return()
        original=owner.MoveObjectsSimultaneouslyIntent;observed=[]
        def wrong(**kwargs):
            observed.append(kwargs['destination']);return original(**{**kwargs,'destination':'exile'})
        with patch.object(owner,'MoveObjectsSimultaneouslyIntent',side_effect=wrong):
            with self.assertRaises(AssertionError):self.test_result_uses_original_incarnation_after_cost_and_distinguishes_copied_controller_return()
        self.assertEqual(['hand'],observed)


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
import test_turn_life_loss_recovery as recovery_helpers
from scripts.build_test_database import build_fixture_database

ORIGINALS=('Controvert','Grim Harvest','Icefall','Krovikan Rot','Resize',"Sun's Bounty")


class RecoverNativeTests(InheritedNativeFixture,unittest.TestCase):
    start=recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject=recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish=recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/recover-cards.json'],folder/'recover.sqlite3')
        cls.db=CardDatabase(folder/'recover.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Actual fixed Recover',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_all_six_complete_originals_close_normally_with_their_printed_spell_results(self):
        s,_=self.start(722000)
        for name in ORIGINALS:
            with self.subTest(name=name):
                card=self.add(s.engine,name,zone='hand');self.assert_closed(s,card)

    def test_actual_printed_bounty_cast_creature_death_pay_or_decline_preserves_privacy_and_replay(self):
        for index,paid in enumerate((True,False)):
            with self.subTest(paid=paid):
                s,private=self.start(722001+index);source=self.add(s.engine,"Sun's Bounty",zone='hand')
                bear=self.add(s.engine,'Grizzly Bears');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
                for card in (source,bear,bolt):self.assert_closed(s,card)
                self.prepare(s,mana={'C':2,'W':2,'R':1});self.checkpoint(s)
                self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual(44,s.state.players['A'].life)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.cast(s,'A',bolt,[bear.ref]);decision=self.resolve(s);self.assertIsNotNone(decision)
                self.assertEqual('semantic.choice',decision.kind);s=self.replay(s,load=True)
                command={'action_id':'choose','pay':paid};self.reject(s,'D',command)
                accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual('hand' if paid else 'exile',s.state.cards[source.object_id].zone)
                self.finish(s,private)

    def actual_spell_and_price(self,name,seed):
        s,private=self.start(seed);source=self.add(s.engine,name,zone='hand')
        own=self.add(s.engine,'Grizzly Bears',seat='A',ref='recover-own-death')
        target=self.add(s.engine,'Grizzly Bears',seat='B',ref='recover-spell-target')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        spell_target=target.ref
        if name=='Grim Harvest':
            dead=self.add(s.engine,'Memnite',zone='graveyard');self.assert_closed(s,dead);spell_target=dead.ref
        elif name=='Icefall':
            artifact=self.add(s.engine,'Sol Ring',seat='B');self.assert_closed(s,artifact);spell_target=artifact.ref
        response=self.add(s.engine,'Shock',seat='B',zone='hand') if name=='Controvert' else None
        for card in (source,own,target,bolt):self.assert_closed(s,card)
        if response is not None:self.assert_closed(s,response)
        s.state.players['B'].mana_pool.update({'R':1});self.prepare(s,mana={'C':15,'W':6,'U':6,'B':6,'R':6,'G':6});self.checkpoint(s)
        if response is not None:
            self.cast(s,'B',response,['B']);spell_target=s.state.stack[-1].ref
        self.cast(s,'A',source,[spell_target]);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        if name=='Grim Harvest':self.assertEqual('hand',s.state.cards[dead.object_id].zone)
        elif name=='Icefall':self.assertEqual('graveyard',s.state.cards[artifact.object_id].zone)
        elif name=='Krovikan Rot':self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        elif name=='Resize':self.assertEqual(5,s.engine._numeric_stat(target.object_id,'power'))
        elif name=='Controvert':self.assertEqual(40,s.state.players['B'].life)
        self.cast(s,'A',bolt,[own.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.replay(s,load=True)
        before=dict(s.state.players['A'].mana_pool);command={'action_id':'choose','pay':True};self.reject(s,'D',command)
        accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        expected={'Controvert':4,'Grim Harvest':3,'Icefall':2,'Krovikan Rot':3,'Resize':2}[name]
        self.assertEqual(expected,sum(before.values())-sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_controvert_counter_result_and_four_mana_recover_price(self):
        self.actual_spell_and_price('Controvert',722010)

    def test_actual_grim_harvest_return_result_and_three_mana_recover_price(self):
        self.actual_spell_and_price('Grim Harvest',722011)

    def test_actual_icefall_artifact_result_and_two_red_recover_price(self):
        self.actual_spell_and_price('Icefall',722012)

    def test_actual_krovikan_rot_power_restricted_result_and_recover_price(self):
        self.actual_spell_and_price('Krovikan Rot',722013)

    def test_actual_resize_temporary_modifier_and_recover_price(self):
        self.actual_spell_and_price('Resize',722014)

    def activate_card(self,s,source,seat='A',targets=()):
        self.priority(s,seat);card=s.state.cards[source.object_id]
        offered=next(a for a in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:'+card.ref+':'))
        command={'action_id':offered['id'],'pay':'auto','targets':list(targets)}
        self.reject(s,'D' if seat!='D' else 'C',command)
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_actual_unpayable_recover_rejects_payment_and_exiles_on_decline(self):
        s,private=self.start(722020);source=self.add(s.engine,"Sun's Bounty",zone='hand')
        mogg=self.add(s.engine,'Mogg Fanatic')
        for card in (source,mogg):self.assert_closed(s,card)
        self.prepare(s,mana={'W':2});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.activate_card(s,mogg,targets=('B',))
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.reject(s,'A',{'action_id':'choose','pay':True})
        s=self.replay(s,load=True);before=dict(s.state.players['A'].mana_pool)
        result=s.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertEqual(before,dict(s.state.players['A'].mana_pool));self.finish(s,private)

    def test_actual_departed_source_keeps_optional_payment_without_moving_exiled_card(self):
        s,private=self.start(722021);source=self.add(s.engine,"Sun's Bounty",zone='graveyard')
        bear=self.add(s.engine,'Grizzly Bears');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        crypt=self.add(s.engine,"Tormod's Crypt",seat='B')
        for card in (source,bear,bolt,crypt):self.assert_closed(s,card)
        self.prepare(s,mana={'C':1,'W':1,'R':1});self.checkpoint(s)
        self.cast(s,'A',bolt,[bear.ref]);self.resolve_top(s)
        self.activate_card(s,crypt,'B',('A',));self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual('exile',s.state.cards[source.object_id].zone);s=self.replay(s,load=True)
        before=sum(s.state.players['A'].mana_pool.values())
        accepted=s.act('pilot:A',{'action_id':'choose','pay':True});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(before-2,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual('exile',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_reentered_source_is_not_returned_or_exiled_by_old_recover(self):
        for index,paid in enumerate((True,False)):
            with self.subTest(paid=paid):
                s,private=self.start(722022+index);source=self.add(s.engine,"Sun's Bounty",zone='graveyard')
                bear=self.add(s.engine,'Grizzly Bears');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
                crypt=self.add(s.engine,"Tormod's Crypt",seat='B');pull=self.add(s.engine,'Pull from Eternity',zone='hand')
                for card in (source,bear,bolt,crypt,pull):self.assert_closed(s,card)
                original=source.logical_object_id;self.prepare(s,mana={'C':1,'W':2,'R':1});self.checkpoint(s)
                self.cast(s,'A',bolt,[bear.ref]);self.resolve_top(s);self.activate_card(s,crypt,'B',('A',))
                self.resolve_top(s);self.cast(s,'A',pull,[s.state.cards[source.object_id].ref])
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                current=s.state.cards[source.object_id];self.assertEqual('graveyard',current.zone)
                self.assertNotEqual(original,current.logical_object_id);identity=current.logical_object_id
                s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','pay':paid})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                self.assertEqual(('graveyard',identity),(s.state.cards[source.object_id].zone,s.state.cards[source.object_id].logical_object_id))
                self.finish(s,private)

    def test_actual_simultaneous_deaths_do_not_trigger_newly_arriving_manifested_recover(self):
        s,private=self.start(722030);source=self.add(s.engine,"Sun's Bounty",zone='library')
        manifest=self.add(s.engine,'Soul Summons',zone='hand');sweep=self.add(s.engine,'Pyroclasm',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears');mogg=self.add(s.engine,'Mogg Fanatic')
        for card in (source,manifest,sweep,bear,mogg):self.assert_closed(s,card)
        library=s.state.players['A'].zones['library'];library.remove(source.object_id);library.append(source.object_id)
        self.prepare(s,mana={'C':4,'W':3,'R':3});self.checkpoint(s)
        self.cast(s,'A',manifest);self.assertIsNone(self.resolve(s));self.assertTrue(s.state.cards[source.object_id].face_down)
        self.assertNotIn("Sun's Bounty",str(s.packet('pilot:B',full=True)))
        self.cast(s,'A',sweep);self.assertIsNone(self.resolve(s))
        for card in (source,bear,mogg):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assertEqual([],s.state.stack);self.finish(s,private)

    def test_actual_token_death_triggers_but_nontoken_artifact_sacrifice_does_not(self):
        s,private=self.start(722031);source=self.add(s.engine,"Sun's Bounty",zone='graveyard')
        raise_=self.add(s.engine,'Raise the Alarm',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        crypt=self.add(s.engine,"Tormod's Crypt")
        for card in (source,raise_,bolt,crypt):self.assert_closed(s,card)
        self.prepare(s,mana={'C':4,'W':3,'R':3});self.checkpoint(s)
        self.activate_card(s,crypt,targets=('B',));self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.cast(s,'A',raise_);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.zone=='battlefield' and not c.is_card_object and c.controller=='A']
        self.assertEqual(2,len(tokens));self.cast(s,'A',bolt,[tokens[0].ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_creature_graveyard_owner_selects_recover_not_last_controller(self):
        for index,owner in enumerate(('A','B')):
            with self.subTest(owner=owner):
                s,private=self.start(722032+index);a=self.add(s.engine,"Sun's Bounty",zone='graveyard')
                b=self.add(s.engine,'Resize',seat='B',zone='graveyard');bear=self.add(s.engine,'Grizzly Bears',seat=owner)
                caster='B' if owner=='A' else 'A';steal=self.add(s.engine,'Act of Treason',seat=caster,zone='hand')
                bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
                for card in (a,b,bear,steal,bolt):self.assert_closed(s,card)
                s.state.players['A'].mana_pool.update({'C':4,'R':3});s.state.players['B'].mana_pool.update({'C':4,'R':3})
                self.prepare(s,actor=caster,active=caster,mana={'C':4,'R':3});self.checkpoint(s)
                self.cast(s,caster,steal,[bear.ref]);self.assertIsNone(self.resolve(s));self.assertEqual(caster,s.state.cards[bear.object_id].controller)
                self.cast(s,'A',bolt,[bear.ref]);self.resolve_top(s)
                sources=[i.source_object_id for i in s.state.stack if i.source_object_id in (a.object_id,b.object_id)]
                expected=a if owner=='A' else b;other=b if owner=='A' else a
                self.assertEqual([expected.object_id],sources);self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.assertEqual(['pilot:'+owner],s.pending_principals());s=self.replay(s,load=True)
                accepted=s.act('pilot:'+owner,{'action_id':'choose','pay':False});self.assertTrue(accepted.ok,accepted.summary)
                self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[expected.object_id].zone)
                self.assertEqual('graveyard',s.state.cards[other.object_id].zone);self.finish(s,private)

    def test_actual_copied_trigger_pays_separately_and_old_decline_cannot_exile_returned_source(self):
        s,private=self.start(722034);source=self.add(s.engine,"Sun's Bounty",zone='graveyard')
        bear=self.add(s.engine,'Grizzly Bears');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        copier=self.add(s.engine,'Strionic Resonator')
        for card in (source,bear,bolt,copier):self.assert_closed(s,card)
        self.prepare(s,mana={'C':4,'W':2,'R':3});self.checkpoint(s)
        self.cast(s,'A',bolt,[bear.ref]);self.resolve_top(s)
        original=next(i.ref for i in s.state.stack if i.source_object_id==source.object_id)
        self.activate_card(s,copier,targets=(original,));self.assertEqual('semantic.choice',self.resolve(s).kind)
        s=self.replay(s,load=True);before=sum(s.state.players['A'].mana_pool.values())
        accepted=s.act('pilot:A',{'action_id':'choose','pay':True});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(before-2,sum(s.state.players['A'].mana_pool.values()))
        accepted=s.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_recast_recover_source_has_a_new_lifetime_and_triggers_again(self):
        s,private=self.start(722035);source=self.add(s.engine,"Sun's Bounty",zone='hand')
        first=self.add(s.engine,'Grizzly Bears',ref='first-recover-death');second=self.add(s.engine,'Grizzly Bears',ref='second-recover-death')
        bolts=[self.add(s.engine,'Lightning Bolt',zone='hand',ref='repeat-bolt-'+str(i)) for i in range(2)]
        for card in (source,first,second,*bolts):self.assert_closed(s,card)
        self.prepare(s,mana={'C':8,'W':6,'R':6});self.checkpoint(s)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));first_identity=s.state.cards[source.object_id].logical_object_id
        self.cast(s,'A',bolts[0],[first.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':True});self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(first_identity,s.state.cards[source.object_id].logical_object_id)
        self.cast(s,'A',bolts[1],[second.ref]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[source.object_id].zone)
        self.assertEqual(48,s.state.players['A'].life);self.finish(s,private)

    def test_actual_recover_can_pay_with_untapped_lands_after_cast_spent_its_mana(self):
        s,private=self.start(722036);source=self.add(s.engine,"Sun's Bounty",zone='hand');mogg=self.add(s.engine,'Mogg Fanatic')
        lands=[self.add(s.engine,'Plains',ref='recover-auto-land-'+str(i)) for i in range(4)]
        for card in (source,mogg,*lands):self.assert_closed(s,card)
        self.prepare(s);self.checkpoint(s);self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,sum(s.state.cards[c.object_id].tapped for c in lands));self.activate_card(s,mogg,targets=('B',))
        self.assertEqual('semantic.choice',self.resolve(s).kind);s=self.replay(s,load=True)
        accepted=s.act('pilot:A',{'action_id':'choose','pay':True,'plan':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.assertTrue(all(s.state.cards[c.object_id].tapped for c in lands));self.finish(s,private)

    def test_actual_replaced_creature_departure_is_not_a_recover_death(self):
        s,private=self.start(722037);source=self.add(s.engine,"Sun's Bounty",zone='graveyard')
        bear=self.add(s.engine,'Grizzly Bears');samurai=self.add(s.engine,'Samurai of the Pale Curtain',seat='B')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        for card in (source,bear,samurai,bolt):self.assert_closed(s,card)
        self.prepare(s,mana={'R':2,'C':2,'W':1});self.checkpoint(s);self.cast(s,'A',bolt,[bear.ref])
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[bear.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.assertEqual([],s.state.stack);self.finish(s,private)

    def test_actual_creature_spell_only_mana_cannot_pay_recover_resolution_cost(self):
        s,private=self.start(722038);source=self.add(s.engine,"Sun's Bounty",zone='hand')
        mogg=self.add(s.engine,'Mogg Fanatic')
        lands=[self.add(s.engine,'Ancient Ziggurat',ref='restricted-recover-'+str(i)) for i in range(2)]
        for card in (source,mogg,*lands):self.assert_closed(s,card)
        self.prepare(s,mana={'W':2});self.checkpoint(s);self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in lands))
        self.activate_card(s,mogg,targets=('B',));self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.reject(s,'A',{'action_id':'choose','pay':True,'plan':'auto'})
        self.assertTrue(all(not s.state.cards[c.object_id].tapped for c in lands))
        accepted=s.act('pilot:A',{'action_id':'choose','pay':False});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_paid_return_fault_rolls_back_mana_zone_events_and_retries_once(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.effect_runtime.zones_and_attachments import HANDLERS
        s,private=self.start(722040);source=self.add(s.engine,"Sun's Bounty",zone='graveyard')
        mogg=self.add(s.engine,'Mogg Fanatic');self.assert_closed(s,source);self.assert_closed(s,mogg)
        self.prepare(s,mana={'C':1,'W':1});self.checkpoint(s);self.activate_card(s,mogg,targets=('B',))
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        original=HANDLERS['recover_source_result'];observed=[]
        def fault(*args,**kwargs):
            result=original(*args,**kwargs);observed.append((s.state.cards[source.object_id].zone,sum(s.state.players['A'].mana_pool.values())))
            raise GameRuleError('Injected postpaid Recover movement failure')
        before=authoritative_state_hash(s.state);command={'action_id':'choose','pay':True}
        with patch.dict(HANDLERS,{'recover_source_result':fault}):
            result=s.act('pilot:A',command);self.assertFalse(result.ok);self.assertIn('Injected postpaid',result.summary)
        self.assertEqual([('hand',0)],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);s=self.replay(s,load=True)
        accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual(39,s.state.players['B'].life);self.finish(s,private)

    def test_fresh_native_paid_return_exile_mutant_is_killed_after_real_grim_harvest_price(self):
        from unittest.mock import patch
        import quorune.effect_runtime.recover as owner
        original=owner.MoveObjectsSimultaneouslyIntent;observed=[]
        def wrong(**kwargs):
            observed.append(kwargs['destination']);return original(**{**kwargs,'destination':'exile'})
        with patch.object(owner,'MoveObjectsSimultaneouslyIntent',side_effect=wrong):
            with self.assertRaises(AssertionError):self.actual_spell_and_price('Grim Harvest',722041)
        self.assertEqual(['hand'],observed)

    def test_genuinely_verified_recover_trigger_and_result_withdrawals_block_complete_parents(self):
        import copy
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_bound_effect_programs import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for cap in ('keyword.recover.fixed_mana','zone.move.recover_source'):
            with self.subTest(cap=cap):
                changed=copy.deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['Recover owner withdrawn'])
                registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
                for name in ORIGINALS:self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)
                for text,type_ in (('You gain 4 life.','Sorcery'),('At the beginning of your upkeep, you may return this card from your graveyard to your hand.','Creature')):
                    self.assertEqual('exact',compile_oracle_card(record(text,type_),capability_registry=registry,capability_profile='commander_review').status)


if __name__=='__main__':unittest.main()
