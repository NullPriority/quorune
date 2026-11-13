"""CR700.2: one selected mode owns its complete grouped target plan."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_fixed_creature_power_damage as records
import test_target_recipient_results as recipients
import optional_result_followup_fixture as turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry,capability_dependencies_for_node
from quorune.rules.modal_capability_shapes import fixed_choose_one_modal_branches,_choose_one_target_schema
from quorune.targets import target_plan,mode_effects
from quorune.card_programs.binding import bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database

CAP='choice.modal.fixed_one'
DUEL='Choose one —\n• Target creature fights another target creature.\n• You gain 2 life.'


class GroupedChooseOneCompilerTests(unittest.TestCase):
    def compile(self,text=DUEL,registry=None):
        return compile_oracle_card(records.card_record(text),capability_registry=registry or load_default_capability_registry(),capability_profile='commander_review')

    def test_grouped_child_closure_preserves_roles_constraints_and_mode_local_references(self):
        ir=self.compile();self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        branches=fixed_choose_one_modal_branches(effects=node.effects,target_schema=node.target_schema,mechanic_ids=node.mechanics)
        self.assertEqual(2,len(branches));self.assertIsNone(branches[1].target_schema)
        self.assertEqual(['fighter','opponent'],[g['id'] for g in branches[0].target_schema['groups']])
        self.assertTrue(branches[0].target_schema['globally_distinct'])
        plan=target_plan(node.target_schema,['mode_1']);self.assertTrue(plan.globally_distinct)
        self.assertFalse(target_plan(node.target_schema,['mode_2']).globally_distinct)
        self.assertIn(CAP,node.capability_dependencies)
        self.assertIn('damage.creature_power.fixed_interaction',node.capability_dependencies)
        self.assertEqual(('$target.0','$target.1'),tuple(mode_effects(node.target_schema,['mode_1'])[0][k] for k in ('source','target')))
        self.assertEqual(['life'],[e['op'] for e in mode_effects(node.target_schema,['mode_2'])])
        child=deepcopy(branches[0].target_schema);child['same_player_groups']=[['fighter','opponent']]
        valid,retained=_choose_one_target_schema(child);self.assertTrue(valid);self.assertEqual(child,retained)
        wrapped={'mode_count':1,'modes':{'mode_1':child}}
        self.assertEqual((('fighter','opponent'),),target_plan(wrapped,['mode_1']).same_player_groups)
        self.assertEqual((),target_plan(node.target_schema,['mode_2']).groups)

    def test_malformed_grouped_schemas_nested_modes_and_unowned_children_fail_closed(self):
        node=self.compile().faces[0].nodes[0]
        for mutation in ('duplicate','unknown','nested','flag','pair','reference','effect','mechanic'):
            schema=deepcopy(node.target_schema);d=schema['modes']['mode_1']
            if mutation=='duplicate':d['groups'][1]['id']='fighter'
            elif mutation=='unknown':d['unowned_constraint']=True
            elif mutation=='nested':d['modes']={'inner':{}}
            elif mutation=='flag':d['globally_distinct']='yes'
            elif mutation=='pair':d['same_player_groups']=[['fighter','unknown']]
            elif mutation=='reference':d['groups'][1]['different_from_groups']=['unknown']
            elif mutation=='effect':d['effects'][0]['source']='$target.2'
            else:d['mechanics']=[]
            self.assertEqual((),capability_dependencies_for_node(effects=(),target_schema=schema,mechanic_ids=node.mechanics),mutation)
        for modes in ([],['mode_1','mode_2'],['mode_1','mode_1'],['missing']):
            with self.assertRaises(ValueError):target_plan(node.target_schema,modes)

    def test_legacy_untargeted_and_single_target_mode_encodings_keep_their_shape(self):
        ir=self.compile('Choose one —\n• Destroy target artifact.\n• You gain 2 life.')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        d=ir.faces[0].nodes[0].target_schema['modes']
        self.assertNotIn('groups',d['mode_1']);self.assertEqual([],d['mode_2']['groups'])
        self.assertEqual(1,len(target_plan(ir.faces[0].nodes[0].target_schema,['mode_1']).groups))

    def test_verified_child_withdrawal_blocks_the_whole_modal_but_retains_life(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']=='damage.creature_power.fixed_interaction').update(status='blocked',blockers=['grouped child withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',self.compile(registry=registry).status)
        self.assertEqual('exact',self.compile('You gain 2 life.',registry=registry).status)


class GroupedChooseOneNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    finish_turn=turns.OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(basic),encoding='utf-8')
        path=folder/'grouped-modal.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/grouped-choose-one-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Grouped Choose one',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,fighter='Hill Giant',recipient='Grizzly Bears',before=None):
        s=self.session(2021015+len(name));spell=self.add(s.engine,name,zone='hand')
        first=self.add(s.engine,fighter,ref='grouped-modal-source')
        second=self.add(s.engine,recipient,seat='B',ref='grouped-modal-opponent')
        artifact=self.add(s.engine,'Mind Stone',seat='B',ref='grouped-modal-artifact')
        enchantment=self.add(s.engine,'Hardened Scales',seat='B',ref='grouped-modal-enchantment')
        vehicle=self.add(s.engine,'Sky Skiff',seat='B',ref='grouped-modal-vehicle')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='grouped-modal-private-C')
        extras={'artifact':artifact,'enchantment':enchantment,'vehicle':vehicle}
        if before is not None:before(s,spell,first,second,extras)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,spell);self.assert_closed(s,first)
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,spell,first,second,extras,private

    def cast_mode(self,s,spell,mode,targets=()):
        self.priority(s,'A');row=self.action(s,'A',spell)
        self.assertIn(mode,row['target_schema']['legal_modes'])
        command={'action_id':row['id'],'modes':[mode],'targets':list(targets),'pay':'auto'}
        self.reject(s,'D',command)
        for modes in ([],['mode_1','mode_2'],['not-a-mode']):self.reject(s,'A',{**command,'modes':modes})
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual([mode],s.state.stack[-1].modes)
        return s.state.stack[-1]

    def test_actual_six_originals_resolve_only_the_grouped_damage_mode(self):
        for name,mode,fight,boost,rebound in (('Bushwhack','mode_2',True,0,False),
                ('Decisive Denial','mode_1',True,0,False),('Plow Through','mode_1',True,0,False),
                ('Giantfall','mode_1',False,0,False),("It's Clobberin' Time!",'mode_1',False,0,True),
                ('Brokers Charm','mode_1',False,1,False)):
            with self.subTest(name=name):
                s,spell,first,second,extras,private=self.scene(name)
                row=self.action(s,'A',spell)
                self.assertEqual(['fighter','opponent' if fight else 'recipient'],[g['id'] for g in row['target_schema']['mode_schemas'][mode]['groups']])
                self.reject(s,'A',{'action_id':row['id'],'modes':[mode],'targets':[second.ref,first.ref],'pay':'auto'})
                self.cast_mode(s,spell,mode,[first.ref,second.ref])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[second.object_id].zone)
                self.assertEqual(2 if fight else 0,s.state.cards[first.object_id].marked_damage)
                self.assertEqual(3+boost,s.engine._numeric_stat(first.object_id,'power'))
                self.assertEqual('exile' if rebound else 'graveyard',s.state.cards[spell.object_id].zone)
                for card in extras.values():self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
                self.finish(s,private)

    def test_actual_destroy_and_draw_modes_ignore_the_other_modes_targets_and_effects(self):
        for name,key,mode in (('Giantfall','artifact','mode_2'),('Plow Through','vehicle','mode_2'),
                ('Brokers Charm','enchantment','mode_2'),('Brokers Charm',None,'mode_3')):
            with self.subTest(name=name,mode=mode):
                s,spell,first,second,extras,private=self.scene(name)
                hand=set(s.state.players['A'].zones['hand']);library=len(s.state.players['A'].zones['library'])
                row=self.action(s,'A',spell)
                if name=='Plow Through':self.reject(s,'A',{'action_id':row['id'],'modes':[mode],'targets':[extras['artifact'].ref],'pay':'auto'})
                self.cast_mode(s,spell,mode,[extras[key].ref] if key else [])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                for kind,card in extras.items():self.assertEqual('graveyard' if kind==key else 'battlefield',s.state.cards[card.object_id].zone)
                self.assertEqual((0,0),(s.state.cards[first.object_id].marked_damage,s.state.cards[second.object_id].marked_damage))
                self.assertEqual(3,s.engine._numeric_stat(first.object_id,'power'))
                if key is None:
                    self.assertEqual(library-2,len(s.state.players['A'].zones['library']))
                    drawn=set(s.state.players['A'].zones['hand'])-hand;self.assertEqual(2,len(drawn))
                    for oid in drawn:
                        ref=s.state.cards[oid].ref
                        for viewer in 'BCD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.finish(s,private)

    def test_actual_bushwhack_search_uses_only_its_owners_library_and_one_shuffle(self):
        selected={}
        def before(s,spell,first,second,extras):selected['land']=self.add(s.engine,'Forest',zone='library',ref='bushwhack-private-land')
        s,spell,first,second,extras,private=self.scene('Bushwhack',before=before)
        shuffles=s.state.players['A'].stats.get('shuffle_count',0)
        self.cast_mode(s,spell,'mode_1')
        self.assertEqual('semantic.search',self.resolve(s).kind)
        command={'action_id':'choose','search_cards':[selected['land'].ref]}
        self.reject(s,'D',command);s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[selected['land'].object_id].zone)
        self.assertEqual(shuffles+1,s.state.players['A'].stats.get('shuffle_count',0))
        self.assertEqual((0,0),(s.state.cards[first.object_id].marked_damage,s.state.cards[second.object_id].marked_damage))
        self.finish(s,private)

    def test_actual_decisive_denial_counter_mode_keeps_the_target_controllers_payment(self):
        for pay in (False,True):
            with self.subTest(pay=pay):
                extra={}
                def before(s,spell,first,second,extras):
                    extra['growth']=self.add(s.engine,'Giant Growth',seat='B',zone='hand')
                    s.state.players['B'].mana_pool.update({'G':1,'C':3})
                s,spell,first,second,extras,private=self.scene('Decisive Denial',before=before)
                self.cast(s,'B',extra['growth'],[second.ref])
                self.cast_mode(s,spell,'mode_2',[s.state.stack[-1].ref])
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.assertEqual(['B'],s.state.pending_decision.actors)
                command={'action_id':'choose','pay':pay};self.reject(s,'A',command)
                s=self.replay(s,load=True);result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(0 if pay else 3,s.state.players['B'].mana_pool['C'])
                self.assertEqual(5 if pay else 2,s.engine._numeric_stat(second.object_id,'power'))
                self.assertEqual((0,0),(s.state.cards[first.object_id].marked_damage,s.state.cards[second.object_id].marked_damage))
                self.finish(s,private)

    def test_actual_brokers_charm_keeps_preparation_when_its_damage_recipient_leaves(self):
        extra={}
        def before(s,spell,first,second,extras):
            extra['bounce']=self.add(s.engine,'Unsummon',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=1
        s,spell,first,second,extras,private=self.scene('Brokers Charm',before=before)
        self.cast_mode(s,spell,'mode_1',[first.ref,second.ref])
        self.cast(s,'B',extra['bounce'],[second.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(4,s.engine._numeric_stat(first.object_id,'power'))
        self.assertEqual('hand',s.state.cards[second.object_id].zone)
        self.assertEqual(0,s.state.cards[first.object_id].marked_damage)
        self.finish(s,private)

    def test_grouped_global_distinctness_controls_availability_and_accepted_commands(self):
        def before(s,spell,first,second,extras):s.engine.move_card(second.object_id,'library')
        s,spell,first,second,extras,private=self.scene('Generic Grouped Duel',before=before)
        row=self.action(s,'A',spell);self.assertEqual(['mode_2'],row['target_schema']['legal_modes'])
        self.reject(s,'A',{'action_id':row['id'],'modes':['mode_1'],'targets':[first.ref,first.ref],'pay':'auto'})
        self.cast_mode(s,spell,'mode_2');self.assertIsNone(self.resolve(s))
        self.assertEqual(42,s.state.players['A'].life);self.assertEqual(0,s.state.cards[first.object_id].marked_damage)
        self.finish(s,private)

    def test_actual_opponents_copy_keeps_its_mode_and_rebinds_under_the_copy_controller(self):
        extra={}
        def before(s,spell,first,second,extras):
            extra['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand')
            extra['source']=self.add(s.engine,'Vampire Nighthawk',seat='B',ref='modal-copy-B-source')
            extra['target']=self.add(s.engine,'Wall of Stone',ref='modal-copy-A-target')
            extra['wrong']=self.add(s.engine,'Grizzly Bears',ref='modal-copy-illegal-new-source')
            s.state.players['B'].mana_pool['R']=2
        s,spell,first,second,extras,private=self.scene('Giantfall',before=before)
        self.cast_mode(s,spell,'mode_1',[first.ref,second.ref])
        self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        command={'action_id':'choose','targets':[extra['source'].ref,extra['target'].ref]}
        self.reject(s,'A',command)
        self.reject(s,'B',{**command,'targets':[extra['wrong'].ref,extra['target'].ref]})
        s=self.replay(s,load=True);result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        copied=s.state.stack[-1];self.assertEqual(('B',['mode_1']),(copied.controller,copied.modes))
        self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[extra['target'].object_id].zone)
        self.assertEqual((40,42),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[second.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[extras['artifact'].object_id].zone)
        self.finish(s,private)

    def test_actual_rebound_cast_can_choose_a_new_legal_mode_without_paying_mana(self):
        s,spell,first,second,extras,private=self.scene("It's Clobberin' Time!")
        self.cast_mode(s,spell,'mode_1',[first.ref,second.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[spell.object_id].zone)
        for _ in range(4):self.finish_turn(s)
        self.assertEqual('A',s.state.active_player)
        self.assertEqual('selection.exile_cast',self.resolve(s).kind)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'] if r.get('choice')=='cast')
        command={'action_id':row['id'],'choice':'cast','modes':['mode_2'],'targets':[extras['artifact'].ref]}
        self.reject(s,'D',command);s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(['mode_2'],s.state.stack[-1].modes)
        self.assertEqual(0,s.state.stack[-1].context['mana_spent_total'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[extras['artifact'].object_id].zone)
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.assertEqual(0,s.state.cards[first.object_id].marked_damage)
        self.finish(s,private)

    def test_executable_modal_payload_requires_each_child_without_coverage_annotations(self):
        s,spell,first,second,extras,private=self.scene('Giantfall')
        program=next(p for p in s.engine.semantics.programs_for_oracle(spell.oracle_id,event='resolve') if p.target_schema and 'modes' in p.target_schema)
        for cap in (CAP,'damage.creature_power.fixed_interaction'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_dropping_selected_child_distinctness_is_killed_by_a_fresh_native_witness(self):
        import quorune.selection.targeting as owner
        original=owner.target_plan
        def lost(*args,**kwargs):return replace(original(*args,**kwargs),globally_distinct=False)
        with patch.object(owner,'target_plan',lost):
            witness=type(self)('test_grouped_global_distinctness_controls_availability_and_accepted_commands')
            with self.assertRaises(AssertionError):witness.test_grouped_global_distinctness_controls_availability_and_accepted_commands()
