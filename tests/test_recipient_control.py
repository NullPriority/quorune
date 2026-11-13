"""CR110.2/611.2a/613.1b: resolving actor and control recipient differ."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import FrozenInstanceError
from common import ROOT
import test_fixed_creature_power_damage as records
import test_target_recipient_results as recipient_fixtures
import optional_result_followup_fixture as turn_fixtures
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from quorune.continuous_effect_state import ResolutionEffectSource
from quorune.semantic_runtime.context import ReadOnlyHandlerContext,SemanticSourceContext


class RecipientControlModelTests(unittest.TestCase):
    def context(self):
        return ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='resolved gift',
            seats=tuple('ABCD'),active_seats=tuple('ABCD'),apnap_order=tuple('ABCD'),
            source=SemanticSourceContext(stack_ref='stack',object_id='source',logical_object_id='source@1',card_ref='source-ref'))

    def test_new_recipient_intent_keeps_the_old_acquiring_actor_contract_strict(self):
        from quorune.semantic_runtime.control_intents import GainControlIntent,GiveControlIntent
        from quorune.continuous_effect_model import ContinuousEffectDuration
        source=ResolutionEffectSource('stack','source','source@1','source-ref')
        intent=GiveControlIntent('A','gift','C',source,'resolved gift')
        self.assertEqual(('A','C',ContinuousEffectDuration.ZONE_OBJECT),(intent.actor,intent.controller,intent.duration))
        with self.assertRaises(FrozenInstanceError):intent.controller='B'
        with self.assertRaises(ValueError):GainControlIntent('A','gift','C',ContinuousEffectDuration.ZONE_OBJECT,source,'resolved gift')

    def test_registered_recipient_handler_is_closed_and_null_target_is_a_noop(self):
        from quorune.semantic_runtime.control_handlers import RecipientControlHandler
        effect={'op':'give_control','schema_version':1,'card':'gift','controller':'C','source':'source-ref'}
        plan=RecipientControlHandler().lower(effect,self.context())
        self.assertEqual(('A','C','gift'),(plan.intents[0].actor,plan.intents[0].controller,plan.intents[0].object_ref))
        for change in ({'schema_version':True},{'controller':'Z'},{'extra':1},{'duration':'until_end_of_turn'}):
            with self.subTest(change=change),self.assertRaises(ValueError):RecipientControlHandler().lower({**effect,**change},self.context())
        for change in ({'card':None},{'controller':None}):
            self.assertEqual((),RecipientControlHandler().lower({**effect,**change},self.context()).intents)

    def test_untargeted_choice_is_controller_owned_live_opponent_scoped_and_incarnation_bound(self):
        from quorune.semantic_choices.control_recipient import ControlRecipientChoiceHandler
        from quorune.replacement.immutable import FrozenMap
        holder={'logical':'gift@1'}
        query=SimpleNamespace(seats=tuple('ABCD'),active_seats=tuple('ACD'),
            object=lambda ref,**kw:SimpleNamespace(logical_object_id=holder['logical']))
        effect={'op':'choose_control_recipient','schema_version':1,'player':'A','card':'gift','opponent_only':True}
        context=SimpleNamespace(actor='A',stack_controller='A',stack_ref='stack',query=query)
        handler=ControlRecipientChoiceHandler();prepared=handler.prepare(effect,context)
        self.assertEqual({'field':'recipient','legal_values':['C','D']},prepared.request.choice.choice_schema())
        continuation=SimpleNamespace(effect=prepared.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))
        completed=handler.complete(continuation,{'recipient':'C'},query)
        self.assertEqual(('gift','C'),tuple(completed.prepend_effects[0][k] for k in ('card','controller')))
        for response in ({'recipient':'A'},{'recipient':'B'},{'recipient':'Z'},{'recipient':'C','card':'other'}):
            with self.subTest(response=response),self.assertRaises(ValueError):handler.complete(continuation,response,query)
        continuation.semantic_frame.controller='B'
        with self.assertRaises(ValueError):handler.complete(continuation,{'recipient':'C'},query)
        continuation.semantic_frame.controller='A';holder['logical']='gift@2'
        with self.assertRaises(ValueError):handler.complete(continuation,{'recipient':'C'},query)
        holder['logical']='gift@1'
        for change in ({'schema_version':True},{'opponent_only':1},{'unknown':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):handler.prepare({**effect,**change},context)


class RecipientControlCompilerTests(unittest.TestCase):
    def compile(self,text,*,permanent=False):
        return compile_oracle_card(records.card_record(text,type_line='Creature — Human' if permanent else 'Sorcery'),
            capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_targeted_player_and_permanent_roles_have_distinct_owned_slots(self):
        ir=self.compile('Target opponent gains control of target permanent you control.')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual(('give_control','$target.0','$target.1'),tuple(node.effects[0][k] for k in ('op','controller','card')))
        self.assertEqual(['recipient','permanent'],[g['id'] for g in node.target_schema['groups']])
        self.assertIn('continuous.control.fixed_recipient',node.capability_dependencies)

    def test_source_anaphora_is_bound_only_for_a_source_specific_trigger(self):
        source=self.compile('When this creature enters, target opponent gains control of it.',permanent=True)
        self.assertEqual('exact',source.status,source.material_residuals)
        self.assertEqual('$source.zone_object',source.faces[0].nodes[0].effects[0]['card'])
        other=self.compile('Whenever another creature enters, target opponent gains control of it.',permanent=True)
        self.assertNotEqual('exact',other.status)
        self.assertNotEqual('exact',self.compile('When you cast this spell, target opponent gains control of it.',permanent=True).status)
        choice=self.compile('When this creature enters, an opponent gains control of it.',permanent=True)
        self.assertEqual('exact',choice.status,choice.material_residuals)
        self.assertIsNone(choice.faces[0].nodes[0].target_schema)
        self.assertEqual('choose_control_recipient',choice.faces[0].nodes[0].effects[0]['op'])

    def test_malformed_recipient_subject_and_version_roles_have_no_capability_closure(self):
        from copy import deepcopy
        from quorune.rules.capabilities import capability_dependencies_for_node
        node=self.compile('Target opponent gains control of target permanent you control.').faces[0].nodes[0]
        for change in ({'controller':'$controller'},{'controller':'$target.1'},{'card':'$target.0'},
                {'schema_version':True},{'source':'$target.1'},{'unknown':1}):
            with self.subTest(change=change):
                self.assertEqual((),capability_dependencies_for_node(effects=({**node.effects[0],**change},),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        schema=deepcopy(node.target_schema);schema['groups'][0]['count']=True
        self.assertEqual((),capability_dependencies_for_node(effects=node.effects,target_schema=schema,mechanic_ids=node.mechanics))

    def test_verified_recipient_withdrawal_rejects_gifts_but_keeps_classic_acquisition(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='continuous.control.fixed_recipient').update(status='blocked',blockers=['Withheld recipient contract'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(records.card_record('Target player gains control of target permanent you control.'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(records.card_record('Gain control of target creature.'),capability_registry=registry).status)

    def test_unsupported_untargeted_mana_side_effect_does_not_gain_normal_activation_admission(self):
        self.assertNotEqual('exact',self.compile('{T}: Add {B}{B}{B}{B}. An opponent gains control of this creature.',permanent=True).status)

    def test_removal_followup_binds_the_target_controller_without_a_second_player_target(self):
        ir=self.compile('{T}: Destroy target artifact or creature. That permanent\'s controller gains control of this creature.',permanent=True)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        self.assertEqual(1,node.target_schema['count'])
        self.assertEqual(2,node.effects[1]['schema_version'])
        self.assertEqual({'kind':'target_controller_information','schema_version':1,'index':0},node.effects[1]['controller'])
        self.assertIn('resolution.target_controller_information',node.capability_dependencies)
        for text in ('Destroy target creature. Its owner gains control of this creature.',
                'Destroy up to one target creature. Its controller gains control of this creature.',
                'Destroy target creature. That land\'s controller gains control of this creature.',
                'You may exile target artifact. If you do, its controller gains control of this creature.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text,permanent=True).status)

    def test_target_controller_control_payloads_reject_changed_roles_and_keep_v1_strict(self):
        from quorune.recipient_control_model import validate_recipient_control_effect
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.rules.recipient_control_shapes import recipient_control_runtime_dependencies
        node=self.compile('{T}: Destroy target creature. Its controller gains control of this creature.',permanent=True).faces[0].nodes[0]
        for change in ({'schema_version':1},{'schema_version':True},{'controller':'$controller'},
                {'controller':{'kind':'target_controller_information','schema_version':1,'index':1}},
                {'controller':{'kind':'target_owner_information','schema_version':1,'index':0}},{'card':'$target.0'}):
            with self.subTest(change=change):
                effects=(node.effects[0],{**node.effects[1],**change})
                self.assertEqual((),capability_dependencies_for_node(effects=effects,target_schema=node.target_schema,mechanic_ids=node.mechanics))
                with self.assertRaises(ValueError):recipient_control_runtime_dependencies(SimpleNamespace(effects=effects,target_schema=node.target_schema))
        with self.assertRaises(ValueError):validate_recipient_control_effect({**node.effects[1],'schema_version':1})
        resolved={**node.effects[1],'controller':'C'}
        with self.assertRaises(ValueError):validate_recipient_control_effect(resolved)
        validate_recipient_control_effect(resolved,resolved=True)


class RecipientControlNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipient_fixtures.TargetRecipientNativeTests.reject
    finish=recipient_fixtures.TargetRecipientNativeTests.finish
    finish_turn=turn_fixtures.OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        v=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        v['cards']=[r for r in v['cards'] if r['name']=='Generic Bound Plains'];v['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(v),encoding='utf-8')
        path=folder/'recipient.sqlite3';build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/recipient-control-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Control recipient roles',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,zone='hand',before=None):
        s=self.session(2021020+len(name));source=self.add(s.engine,name,zone=zone)
        gift=self.add(s.engine,'Grizzly Bears',ref='foreign-owned-gift');gift.owner='D'
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='gift-private-C')
        self.add(s.engine,'Fervor')
        if before:before(s,source,gift)
        s.state.config.semantic_policy='trusted_only'
        for card in (source,gift):self.assert_closed(s,card)
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,gift,private

    def activate(self,s,source,targets,**extra):
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':') and not r.get('mana_ability'))
        command={'action_id':row['id'],'targets':targets,'pay':'auto',**extra}
        self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def spell_gifts(self,names):
        for name in names:
            s,spell,gift,private=self.scene(name)
            row=self.action(s,'A',spell)
            self.reject(s,'A',{'action_id':row['id'],'targets':[gift.ref,'C'],'pay':'auto'})
            self.cast(s,'A',spell,['C',gift.ref]);s=self.replay(s,load=True)
            self.assertIsNone(self.resolve(s))
            self.assertEqual(('C','D','battlefield'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner,s.state.cards[gift.object_id].zone))
            self.finish(s,private)

    def test_donate_offering_and_wrong_turn_separate_actor_recipient_owner_and_subject(self):
        self.spell_gifts(('Donate','Harmless Offering','Wrong Turn'))

    def test_bazaar_trader_targets_only_its_controlled_permanent_union(self):
        extra={}
        def before(s,source,gift):extra['enchantment']=self.add(s.engine,'Hardened Scales')
        s,source,gift,private=self.scene('Bazaar Trader',zone='battlefield',before=before)
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':'))
        self.reject(s,'A',{'action_id':row['id'],'targets':['C',extra['enchantment'].ref],'pay':'auto'})
        self.activate(s,source,['C',gift.ref]);self.assertTrue(s.state.cards[source.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','D'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner))
        self.assertEqual('A',s.state.cards[source.object_id].controller);self.finish(s,private)

    def test_humble_defector_draws_for_the_resolver_before_giving_its_original_incarnation(self):
        s,source,gift,private=self.scene('Humble Defector',zone='battlefield')
        hand=set(s.state.players['A'].zones['hand'])
        self.activate(s,source,['C']);self.assertTrue(s.state.cards[source.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        drawn=set(s.state.players['A'].zones['hand'])-hand;self.assertEqual(2,len(drawn))
        for oid in drawn:
            for viewer in 'BCD':self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.finish(s,private)

    def test_sleeper_entry_target_is_chosen_before_priority_and_uses_the_new_controller(self):
        s,source,gift,private=self.scene('Sleeper Agent')
        self.cast(s,'A',source);self.assertEqual('semantic.target',self.resolve(s).kind)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.target(s,'A',['C']);self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        self.finish(s,private)

    def test_horse_uses_an_untargeted_resolution_choice_with_saved_controller_authority(self):
        s,source,gift,private=self.scene('Akroan Horse')
        self.cast(s,'A',source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        for recipient in ('A','Z'):
            self.reject(s,'A',{'action_id':'choose','recipient':recipient})
        self.reject(s,'B',{'action_id':'choose','recipient':'C'})
        s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','recipient':'C'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        self.finish(s,private)

    def test_jinxed_artifact_costs_are_paid_by_the_actor_before_source_control_changes(self):
        for name in ('Jinxed Ring','Jinxed Idol'):
            with self.subTest(name=name):
                s,source,gift,private=self.scene(name,zone='battlefield')
                self.activate(s,source,['C'],cost_cards=[gift.ref])
                self.assertEqual('graveyard',s.state.cards[gift.object_id].zone)
                self.assertIn(gift.object_id,s.state.players['D'].zones['graveyard'])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
                self.assertEqual((40,40,40,40),tuple(s.state.players[p].life for p in 'ABCD'))
                self.finish(s,private)

    def test_witch_engine_is_not_a_mana_ability_and_adds_for_the_original_actor_before_gifting(self):
        s,source,gift,private=self.scene('Witch Engine',zone='battlefield')
        before={p:s.state.players[p].mana_pool.get('B',0) for p in 'AC'}
        self.activate(s,source,['C'])
        self.assertTrue(s.state.stack)
        self.assertEqual(before['A'],s.state.players['A'].mana_pool.get('B',0))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(before['A']+4,s.state.players['A'].mana_pool.get('B',0))
        self.assertEqual(before['C'],s.state.players['C'].mana_pool.get('B',0))
        self.assertEqual('C',s.state.cards[source.object_id].controller);self.finish(s,private)

    def test_wishclaw_searches_and_shuffles_for_the_actor_before_choosing_a_control_recipient(self):
        s,source,gift,private=self.scene('Wishclaw Talisman')
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[source.object_id].counters['wish'])
        selected=s.state.cards[s.state.players['A'].zones['library'][-1]].ref
        shuffles=s.state.players['A'].stats.get('shuffle_count',0)
        self.activate(s,source,[])
        self.assertEqual(2,s.state.cards[source.object_id].counters['wish'])
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual('semantic.search',self.resolve(s).kind)
        command={'action_id':'choose','search_cards':[selected]}
        self.reject(s,'C',command);s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual('A',s.state.cards[source.object_id].controller)
        self.assertEqual(shuffles+1,s.state.players['A'].stats.get('shuffle_count',0))
        for viewer in 'BCD':self.assertNotIn(selected,json.dumps(s.packet('pilot:'+viewer,full=True)))
        result=s.act('pilot:A',{'action_id':'choose','recipient':'C'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('C',2),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].counters['wish']))
        self.finish(s,private)

    def test_avarice_amulet_targets_a_recipient_after_its_attached_creature_dies(self):
        extra={}
        def before(s,source,gift):extra['kill']=self.add(s.engine,'Murder',zone='hand')
        s,source,gift,private=self.scene('Avarice Amulet',before=before)
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.activate(s,source,[gift.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(gift.object_id,s.state.cards[source.object_id].attached_to)
        self.cast(s,'A',extra['kill'],[gift.ref])
        self.assertEqual('semantic.target',self.resolve(s).kind)
        self.assertEqual('graveyard',s.state.cards[gift.object_id].zone)
        s=self.replay(s,load=True)
        self.target(s,'A',['C'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(('C','A',None),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner,s.state.cards[source.object_id].attached_to))
        self.finish(s,private)

    def test_new_controller_upkeeps_apply_sleeper_loss_and_horse_opponent_token_roles(self):
        for name in ('Sleeper Agent','Akroan Horse'):
            with self.subTest(name=name):
                s,source,gift,private=self.scene(name)
                self.cast(s,'A',source);decision=self.resolve(s)
                if name=='Sleeper Agent':self.target(s,'A',['C'])
                else:
                    result=s.act('pilot:A',{'action_id':'choose','recipient':'C'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                self.finish_turn(s);self.finish_turn(s)
                self.until(s,lambda s:s.state.active_player=='C' and s.state.step=='upkeep' and bool(s.state.stack))
                self.assertIsNone(self.resolve(s))
                self.assertEqual('C',s.state.cards[source.object_id].controller)
                if name=='Sleeper Agent':
                    self.assertEqual((40,40,38,40),tuple(s.state.players[p].life for p in 'ABCD'))
                else:
                    tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
                    self.assertEqual(['A','B','D'],sorted(c.controller for c in tokens))
                    for token in tokens:
                        self.assertEqual(token.controller,token.owner)
                        self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
                self.finish(s,private)

    def test_giver_departure_does_not_end_the_gift_but_recipient_departure_restores_base_controller(self):
        for leaving,expected in (('A','C'),('C','A')):
            with self.subTest(leaving=leaving):
                s,source,gift,private=self.scene('Donate')
                self.cast(s,'A',source,['C',gift.ref]);self.assertIsNone(self.resolve(s))
                self.priority(s,leaving)
                result=s.act('pilot:'+leaving,{'action_id':'concede','choices':{'confirm_concede':True}})
                self.assertTrue(result.ok,result.summary)
                self.assertEqual((expected,'D','battlefield'),(s.state.cards[gift.object_id].controller,s.state.cards[gift.object_id].owner,s.state.cards[gift.object_id].zone))
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.replay(s,load=True)

    def test_humble_draws_but_does_not_give_a_returned_source_and_illegal_player_stops_all_results(self):
        for invalidate in ('source','recipient'):
            with self.subTest(invalidate=invalidate):
                extra={}
                def before(s,source,gift):extra['blink']=self.add(s.engine,'Cloudshift',zone='hand')
                s,source,gift,private=self.scene('Humble Defector',zone='battlefield',before=before)
                hand=set(s.state.players['A'].zones['hand']);old=source.logical_object_id
                self.activate(s,source,['C'])
                if invalidate=='source':self.cast(s,'A',extra['blink'],[source.ref])
                else:
                    self.priority(s,'C')
                    result=s.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('A',s.state.cards[source.object_id].controller)
                if invalidate=='source':
                    self.assertNotEqual(old,s.state.cards[source.object_id].logical_object_id)
                    self.assertEqual(2,len(set(s.state.players['A'].zones['hand'])-hand))
                    self.finish(s,private)
                else:
                    self.assertEqual(hand,set(s.state.players['A'].zones['hand']))
                    for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                    self.replay(s,load=True)

    def test_humble_queued_actor_keeps_the_draw_even_after_someone_else_controls_its_source(self):
        extra={}
        def before(s,source,gift):
            extra['theft']=self.add(s.engine,'Turn Against',seat='B',zone='hand')
            s.state.players['B'].mana_pool.update({'C':4,'R':1})
        s,source,gift,private=self.scene('Humble Defector',zone='battlefield',before=before)
        hand={p:set(s.state.players[p].zones['hand']) for p in 'AC'}
        self.activate(s,source,['C']);self.cast(s,'B',extra['theft'],[source.ref]);self.resolve_top(s)
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(set(s.state.players['A'].zones['hand'])-hand['A']))
        self.assertEqual(hand['C'],set(s.state.players['C'].zones['hand']))
        self.assertEqual('C',s.state.cards[source.object_id].controller)
        self.finish(s,private)

    def test_opponent_copy_rebinds_controlled_subject_under_the_copy_actor_and_keeps_the_recipient(self):
        extra={}
        def before(s,source,gift):
            extra['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand')
            extra['gift']=self.add(s.engine,'Grizzly Bears',seat='B',ref='copy-actor-gift');extra['gift'].owner='D'
            extra['wrong']=self.add(s.engine,'Grizzly Bears',ref='new-illegal-copy-subject')
            s.state.players['B'].mana_pool['R']=2
        s,source,gift,private=self.scene('Donate',before=before)
        self.cast(s,'A',source,['C',gift.ref]);self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        command={'action_id':'choose','targets':['C',extra['gift'].ref]}
        self.reject(s,'A',command)
        self.reject(s,'B',{**command,'targets':['C',extra['wrong'].ref]})
        s=self.replay(s,load=True)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('B',s.state.stack[-1].controller)
        self.resolve_top(s)
        self.assertEqual(('C','D'),(s.state.cards[extra['gift'].object_id].controller,s.state.cards[extra['gift'].object_id].owner))
        self.assertIsNone(self.resolve(s));self.assertEqual('C',s.state.cards[gift.object_id].controller)
        self.finish(s,private)

    def test_fresh_donate_witness_kills_the_resolving_actor_as_recipient_mutant(self):
        from unittest.mock import patch
        from quorune.semantic_runtime.control_handlers import RecipientControlHandler
        method='test_donate_offering_and_wrong_turn_separate_actor_recipient_owner_and_subject'
        type(self)(method).spell_gifts(('Donate',))
        original=RecipientControlHandler.lower
        def wrong(handler,effect,context):return original(handler,{**effect,'controller':context.actor},context)
        with patch.object(RecipientControlHandler,'lower',wrong):
            with self.assertRaises(AssertionError):type(self)(method).spell_gifts(('Donate',))

    def test_gift_and_choice_payloads_require_the_recipient_owner_without_coverage(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Donate','Akroan Horse'):
            with self.subTest(name=name):
                s,source,gift,private=self.scene(name)
                program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op') in {'give_control','choose_control_recipient'} for e in p.effects))
                deps=tuple(c for c in program.capability_dependencies if c!='continuous.control.fixed_recipient')
                stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:continuous.control.fixed_recipient',bound['blockers'])

    def starke_departure(self):
        # CR 608.2h: B announces, C controls at departure, D physically owns it.
        extra={}
        def before(s,source,gift):
            gift.controller='B'
            s.state.players['A'].zones['battlefield'].remove(gift.object_id)
            s.state.players['B'].zones['battlefield'].append(gift.object_id)
            extra['theft']=self.add(s.engine,'Turn Against',seat='C',zone='hand')
            s.state.players['C'].mana_pool.update({'C':4,'R':1})
        s,source,gift,private=self.scene('Starke of Rath',zone='battlefield',before=before)
        self.activate(s,source,[gift.ref]);self.cast(s,'C',extra['theft'],[gift.ref]);self.resolve_top(s)
        self.assertEqual('C',s.state.cards[gift.object_id].controller)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('graveyard','D'),(s.state.cards[gift.object_id].zone,s.state.cards[gift.object_id].owner))
        self.assertEqual(('C','A'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].owner))
        self.assertIn(source.object_id,s.state.players['C'].zones['battlefield'])
        self.finish(s,private)

    def test_starke_uses_the_departure_controller_after_a_response_changes_control(self):
        self.starke_departure()

    def test_starke_does_not_give_a_returned_source_or_resolve_against_a_returned_target(self):
        for blink_source in (True,False):
            with self.subTest(blink_source=blink_source):
                extra={}
                def before(s,source,gift):
                    gift.controller='B'
                    s.state.players['A'].zones['battlefield'].remove(gift.object_id)
                    s.state.players['B'].zones['battlefield'].append(gift.object_id)
                    extra['blink']=self.add(s.engine,'Cloudshift',seat='A' if blink_source else 'B',zone='hand')
                    s.state.players['B'].mana_pool['W']=1
                s,source,gift,private=self.scene('Starke of Rath',zone='battlefield',before=before)
                subject=source if blink_source else gift;old=subject.logical_object_id
                self.activate(s,source,[gift.ref]);self.cast(s,'A' if blink_source else 'B',extra['blink'],[subject.ref])
                self.resolve_top(s);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertNotEqual(old,s.state.cards[subject.object_id].logical_object_id)
                self.assertEqual(('A','battlefield'),(s.state.cards[source.object_id].controller,s.state.cards[source.object_id].zone))
                self.assertEqual('graveyard' if blink_source else 'battlefield',s.state.cards[gift.object_id].zone)
                self.finish(s,private)

    def test_starke_gives_control_even_when_indestructibility_prevents_destruction(self):
        extra={}
        def before(s,source,gift):
            extra['target']=self.add(s.engine,'Darksteel Myr',seat='B');extra['target'].owner='D'
        s,source,gift,private=self.scene('Starke of Rath',zone='battlefield',before=before)
        target=extra['target'];self.assert_closed(s,target)
        self.activate(s,source,[target.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(('battlefield','B','D'),tuple(getattr(s.state.cards[target.object_id],k) for k in ('zone','controller','owner')))
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        self.finish(s,private)

    def test_starke_payload_requires_target_information_even_when_coverage_is_removed(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,source,gift,private=self.scene('Starke of Rath',zone='battlefield')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')=='give_control' for e in p.effects))
        for cap in ('resolution.target_controller_information','continuous.control.fixed_recipient'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_fresh_starke_witness_kills_reading_the_announcement_controller_as_recipient(self):
        from unittest.mock import patch
        from quorune.target_controller_information import resolve_target_controller_information
        def wrong(host,value,item):
            resolve_target_controller_information(host,value,item)
            return item.context['target_snapshots'][item.targets[value['index']]]['controller']
        method='test_starke_uses_the_departure_controller_after_a_response_changes_control'
        type(self)(method).starke_departure()
        with patch('quorune.target_controller_information.resolve_target_controller_information',wrong):
            with self.assertRaises(AssertionError):type(self)(method).starke_departure()

    def test_goblin_cadets_gifts_the_source_and_removes_it_from_combat_after_becoming_blocked(self):
        extra={}
        def before(s,source,gift):extra['blocker']=self.add(s.engine,'Hill Giant',seat='B')
        s,source,gift,private=self.scene('Goblin Cadets',zone='battlefield',before=before)
        self.attack(s,[(source,'B')]);self.until(s,lambda s:s.state.pending_decision.kind=='combat.blockers')
        command={'action_id':'block','blocks':{extra['blocker'].ref:[source.ref]}}
        self.reject(s,'C',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.target',self.resolve(s).kind)
        self.target(s,'A',['C']);self.assertIsNone(self.resolve(s))
        self.assertEqual('C',s.state.cards[source.object_id].controller)
        self.assertNotIn(source.object_id,s.state.combat.attackers)
        self.until(s,lambda s:s.state.phase_index>=9)
        self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual(0,s.state.cards[extra['blocker'].object_id].marked_damage)
        self.finish(s,private)
