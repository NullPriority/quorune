"""CR700.2: selected modes retain independent target roles and constraints."""
from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace

import test_fixed_creature_power_damage as records
import test_target_recipient_results as recipients
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from scripts.build_test_database import build_fixture_database
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry,capability_dependencies_for_node
from quorune.rules.modal_capability_shapes import fixed_nonrepeating_modal_branches
from quorune.targets import target_plan,mode_effects

TEXT='Choose one or both —\n• Double target creature\'s power and toughness until end of turn.\n• Target creature you control fights target creature an opponent controls.'


class GroupedNonrepeatingCompilerTests(unittest.TestCase):
    def compile(self,text=TEXT):
        return compile_oracle_card(records.card_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_grouped_branch_closure_preserves_child_ids_and_scopes_only_announced_groups(self):
        ir=self.compile();self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        branches=fixed_nonrepeating_modal_branches(effects=node.effects,target_schema=node.target_schema,mechanic_ids=node.mechanics)
        self.assertEqual(['fighter','opponent'],[g['id'] for g in branches[1].target_schema['groups']])
        self.assertTrue(branches[1].target_schema['globally_distinct'])
        plan=target_plan(node.target_schema,['mode_1','mode_2'])
        self.assertEqual(('mode_1_target_1','mode_2:fighter','mode_2:opponent'),tuple(g.group_id for g in plan.groups))
        self.assertFalse(plan.globally_distinct)
        self.assertEqual(('mode_2:opponent',),plan.groups[1].different_from_groups)
        self.assertNotIn('mode_1_target_1',plan.groups[1].different_from_groups)
        effects=mode_effects(node.target_schema,['mode_1','mode_2'])
        self.assertEqual(('$target.1','$target.2'),(effects[1]['source'],effects[1]['target']))

    def test_multiple_grouped_modes_rebase_local_ids_without_cross_mode_distinctness(self):
        child={'group_scope':'mode','groups':[
            {'id':'one','zones':['battlefield'],'count':1},
            {'id':'two','zones':['battlefield'],'count':1,'different_from_groups':['one']}],
            'globally_distinct':True,'same_player_groups':[['one','two']],
            'effects':[{'op':'test','cards':'$target_group.one','target':'$target.1'}]}
        schema={'min_modes':1,'max_modes':2,'modes':{'mode_1':child,'mode_2':deepcopy(child)}}
        plan=target_plan(schema,['mode_1','mode_2'])
        self.assertEqual(('mode_1:one','mode_1:two','mode_2:one','mode_2:two'),tuple(g.group_id for g in plan.groups))
        self.assertEqual((('mode_1:one','mode_1:two'),('mode_2:one','mode_2:two')),plan.same_player_groups)
        self.assertFalse(plan.globally_distinct)
        self.assertEqual([{'op':'test','cards':'$target_group.mode_1:one','target':'$target.1'},
            {'op':'test','cards':'$target_group.mode_2:one','target':'$target.3'}],mode_effects(schema,['mode_1','mode_2']))

    def test_malformed_local_group_scope_and_unowned_children_fail_closed(self):
        ir=self.compile();self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes
        for mutation in ('scope','duplicate','reference','pair','flag','nested','effect'):
            schema=deepcopy(node.target_schema);d=schema['modes']['mode_2']
            if mutation=='scope':d['group_scope']=True
            elif mutation=='duplicate':d['groups'][1]['id']='fighter'
            elif mutation=='reference':d['groups'][1]['different_from_groups']=['mode_1_target_1']
            elif mutation=='pair':d['same_player_groups']=[['fighter','mode_1_target_1']]
            elif mutation=='flag':d['globally_distinct']='yes'
            elif mutation=='nested':d['modes']={'inner':{}}
            else:d['effects'][0]['source']='$target.3'
            self.assertEqual((),capability_dependencies_for_node(effects=(),target_schema=schema,mechanic_ids=node.mechanics),mutation)

    def test_scoped_group_values_resolve_lists_scalars_and_controller_without_leaking_other_modes(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        item=SimpleNamespace(context={'target_groups':{'mode_1:one':['a'],'mode_2:one':['b']},
            'target_groups_current':{'mode_1:one':[None],'mode_2:one':['b']}})
        host=SimpleNamespace(_target_snapshot=lambda ref:{'controller':'B'})
        self.assertEqual([],resolve_semantic_value(host,'$target_group.mode_1:one',item))
        self.assertIsNone(resolve_semantic_value(host,'$target_group.mode_1:one.0',item))
        self.assertEqual('b',resolve_semantic_value(host,'$target_group.mode_2:one.0',item))
        self.assertEqual('B',resolve_semantic_value(host,'$target_group.mode_2:one.current_controller.0',item))
        with self.assertRaises(ValueError):resolve_semantic_value(host,'$target_group.mode_2:missing',item)

    def test_modal_characteristic_minimum_uses_each_child_schema_without_coverage_annotations(self):
        from quorune.rules.target_characteristic_sets import characteristic_set_runtime_dependencies
        node=self.compile().faces[0].nodes[0]
        required=characteristic_set_runtime_dependencies(SimpleNamespace(effects=(),target_schema=node.target_schema,coverage=()))
        self.assertIn('continuous.resolution.fixed_stat_multiplier',required)
        altered=deepcopy(node.target_schema)
        altered['modes']['mode_1']['groups'][0]['types_any']=['artifact']
        with self.assertRaises(ValueError):characteristic_set_runtime_dependencies(SimpleNamespace(effects=(),target_schema=altered,coverage=()))

    def test_indexed_copy_identity_ignores_a_cache_from_a_different_retargeted_copy(self):
        from quorune.token_copy_references import _identity,COPY_REFERENCE_CONTEXT
        card=SimpleNamespace(object_id='new',logical_object_id='new@0',zone_change_counter=0,ref='new-ref')
        host=SimpleNamespace(state=SimpleNamespace(cards={'new':card}))
        item=SimpleNamespace(targets=['first-ref','new-ref'],context={
            'target_snapshots':{'new-ref':{'object_id':'new','zone_change_counter':0}},
            COPY_REFERENCE_CONTEXT:{'target:1':{'ref':'old-ref','object_id':'old','logical_object_id':'old@0','zone':'battlefield'}}})
        self.assertEqual(('new','new@0','battlefield'),_identity(host,item,'target',target_index=1))
        for index in (True,-1,2,'1'):
            with self.subTest(index=index),self.assertRaises(ValueError):_identity(host,item,'target',target_index=index)
        with self.assertRaises(ValueError):_identity(host,item,'source',target_index=1)

    def test_copy_slot_metadata_requires_the_explicit_new_scope_and_legacy_modes_keep_their_bytes(self):
        text="Choose two —\n• Create a token that's a copy of target creature you control.\n• Target creature you control gets +3/+3 until end of turn.\n• Target creature you control fights target creature an opponent controls."
        node=self.compile(text).faces[0].nodes[0]
        effects=mode_effects(node.target_schema,['mode_1','mode_2'])
        self.assertEqual({'schema_version':1,'index':0},effects[0]['_copy_target'])
        self.assertEqual('$target.1',effects[1]['card'])
        legacy=deepcopy(node.target_schema);legacy['modes'].pop('mode_3')
        self.assertNotIn('_copy_target',mode_effects(legacy,['mode_1','mode_2'])[0])

    def test_optional_copy_slot_binding_visits_executable_children_and_ignores_future_metadata(self):
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        recipe=TokenCopyRecipeSpec(origin='target').to_dict()
        copy={'op':'create_token','copy_spec':recipe}
        child={'groups':[{'id':'target','count':1}],'group_scope':'mode',
            'effects':[{'op':'offer_optional_effect','effects':[copy]},
                {'op':'create_token','characteristics':{'effects':[copy]}}]}
        schema={'mode_count':1,'modes':{'mode_1':child}}
        effects=mode_effects(schema,['mode_1'])
        self.assertEqual({'schema_version':1,'index':0},effects[0]['effects'][0]['_copy_target'])
        self.assertNotIn('_copy_target',effects[1]['characteristics']['effects'][0])

    def test_indexed_copy_reads_its_pinned_departure_snapshot_and_not_the_returned_incarnation(self):
        from unittest.mock import patch
        from quorune.token_copy_recipes import TokenCopyRecipeSpec
        from quorune.token_copy_references import copy_reference_snapshot,COPY_REFERENCE_CONTEXT
        card=SimpleNamespace(object_id='object',logical_object_id='object@1',zone_change_counter=1,ref='ref',zone='battlefield')
        old={'object_id':'object','logical_object_id':'object@0','ref':'ref','zone':'battlefield','snapshot':{'power':'7'}}
        item=SimpleNamespace(targets=['other','ref'],context={'target_snapshots':{'ref':{'object_id':'object','zone_change_counter':0}},
            COPY_REFERENCE_CONTEXT:{'target:1':old}})
        host=SimpleNamespace(state=SimpleNamespace(cards={'object':card}))
        with patch('quorune.token_copy_references._snapshot') as current:
            self.assertEqual(old,copy_reference_snapshot(host,item,TokenCopyRecipeSpec(origin='target'),target_index=1))
            current.assert_not_called()
        item.context['target_snapshots']['ref']['zone_change_counter']=1
        new={**old,'logical_object_id':'object@1','snapshot':{'power':'9'}}
        with patch('quorune.token_copy_references._snapshot',return_value=new):
            self.assertEqual(new,copy_reference_snapshot(host,item,TokenCopyRecipeSpec(origin='target'),target_index=1))

    def test_verified_child_or_modal_capability_withdrawal_withholds_the_complete_program(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        source=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap in ('choice.modal.fixed_nonrepeating','damage.creature_power.fixed_interaction','continuous.resolution.fixed_stat_multiplier'):
            raw=deepcopy(source);next(r for r in raw['capabilities'] if r['id']==cap).update(status='blocked',blockers=['Withheld for modal ownership witness'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(records.card_record(TEXT),capability_registry=registry,capability_profile='commander_review').status)
            self.assertEqual('exact',compile_oracle_card(records.card_record('You gain 2 life.'),capability_registry=registry,capability_profile='commander_review').status)


class GroupedNonrepeatingNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        v=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        v['cards']=[c for c in v['cards'] if c['name']=='Generic Bound Plains'];v['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(v),encoding='utf-8')
        path=folder/'nonrepeating.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/grouped-nonrepeating-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Grouped multiple modes',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,fighter='Grizzly Bears',recipient='Hill Giant',before=None):
        s=self.session(2021018+len(name));spell=self.add(s.engine,name,zone='hand')
        first=self.add(s.engine,fighter,ref='nonrepeating-source')
        second=self.add(s.engine,recipient,seat='B',ref='nonrepeating-opponent')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='nonrepeating-private-C')
        if before:before(s,spell,first,second)
        s.state.config.semantic_policy='trusted_only'
        for card in (spell,first,second):self.assert_closed(s,card)
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,spell,first,second,private

    def cast_modes(self,s,spell,modes,targets):
        self.priority(s,'A');row=self.action(s,'A',spell)
        command={'action_id':row['id'],'modes':modes,'targets':targets,'pay':'auto'}
        self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(sorted(modes),s.state.stack[-1].modes)
        return s.state.stack[-1]

    def test_originals_execute_grouped_modes_after_their_printed_preparation(self):
        for name,modes,fighter,recipient in (('Epic Fight',['mode_2','mode_1'],'Grizzly Bears','Hill Giant'),
                ('Markov Retribution',['mode_1','mode_2'],'Vampire Nighthawk','Hill Giant'),
                ("Brigid's Command",['mode_4','mode_3'],'Grizzly Bears','Hill Giant')):
            with self.subTest(name=name):
                s,spell,first,second,private=self.scene(name,fighter=fighter,recipient=recipient)
                targets=[first.ref,second.ref] if name=='Markov Retribution' else [first.ref,first.ref,second.ref]
                # Another target in Markov's damage mode cannot be its source.
                if name=='Markov Retribution':
                    row=self.action(s,'A',spell)
                    self.reject(s,'A',{'action_id':row['id'],'modes':modes,'targets':[first.ref,first.ref],'pay':'auto'})
                self.cast_modes(s,spell,modes,targets)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard',s.state.cards[second.object_id].zone)
                self.assertEqual('battlefield',s.state.cards[first.object_id].zone)
                power=4 if name=='Epic Fight' else 3 if name=='Markov Retribution' else 5
                self.assertEqual(power,s.engine._numeric_stat(first.object_id,'power'))
                self.assertEqual(0 if name=='Markov Retribution' else 3,s.state.cards[first.object_id].marked_damage)
                self.assertEqual(43 if name=='Markov Retribution' else 40,s.state.players['A'].life)
                self.finish(s,private)

    def test_clash_fight_then_remove_then_add_lore_reaches_the_actual_saga_chapter_once(self):
        extra={}
        def before(s,spell,first,second):
            extra['saga']=self.add(s.engine,'History of Benalia');extra['saga'].counters['lore']=2
            self.assert_closed(s,extra['saga'])
        s,spell,first,second,private=self.scene('Clash of the Eikons',fighter='Hill Giant',recipient='Grizzly Bears',before=before)
        self.cast_modes(s,spell,['mode_3','mode_1','mode_2'],[first.ref,second.ref,extra['saga'].ref,extra['saga'].ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[second.object_id].zone)
        self.assertEqual(2,s.state.cards[first.object_id].marked_damage)
        self.assertEqual(2,s.state.cards[extra['saga'].object_id].counters['lore'])
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(1,len(tokens));self.assertEqual('A',tokens[0].controller)
        self.assertEqual((2,2),tuple(s.engine._numeric_stat(tokens[0].object_id,stat) for stat in ('power','toughness')))
        self.assertIn('Vigilance',s.engine._effective_card_data(tokens[0])['keywords'])
        self.finish(s,private)

    def test_original_one_mode_selections_leave_the_other_modes_unexecuted(self):
        for name,mode in (('Epic Fight','mode_1'),('Epic Fight','mode_2'),
                ('Markov Retribution','mode_1'),('Markov Retribution','mode_2')):
            with self.subTest(name=name,mode=mode):
                s,spell,first,second,private=self.scene(name,fighter='Vampire Nighthawk' if name=='Markov Retribution' else 'Grizzly Bears')
                targets=[] if name=='Markov Retribution' and mode=='mode_1' else [first.ref] if mode=='mode_1' else [first.ref,second.ref]
                self.cast_modes(s,spell,[mode],targets)
                self.assertIsNone(self.resolve(s))
                if name=='Epic Fight' and mode=='mode_2':
                    self.assertEqual('graveyard',s.state.cards[first.object_id].zone)
                    self.assertEqual(2,s.state.cards[second.object_id].marked_damage)
                else:
                    power=4 if name=='Epic Fight' else 3 if mode=='mode_1' else 2
                    self.assertEqual(power,s.engine._numeric_stat(first.object_id,'power'))
                    self.assertEqual(0,s.state.cards[first.object_id].marked_damage)
                    self.assertEqual(0,s.state.cards[second.object_id].marked_damage)
                self.assertEqual('graveyard' if name=='Markov Retribution' and mode=='mode_2' else 'battlefield',s.state.cards[second.object_id].zone)
                self.assertEqual(42 if name=='Markov Retribution' and mode=='mode_2' else 40,s.state.players['A'].life)
                self.finish(s,private)

    def test_brigid_copy_precedes_pump_and_another_pair_creates_for_the_target_player(self):
        for pair in (('mode_1','mode_3'),('mode_2','mode_3')):
            with self.subTest(pair=pair):
                s,spell,first,second,private=self.scene("Brigid's Command",fighter='Goldmeadow Harrier')
                self.cast_modes(s,spell,list(reversed(pair)),[first.ref if pair[0]=='mode_1' else 'D',first.ref])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(4,s.engine._numeric_stat(first.object_id,'power'))
                tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
                self.assertEqual(1,len(tokens));token=tokens[0]
                self.assertEqual('A' if pair[0]=='mode_1' else 'D',token.controller)
                self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
                if pair[0]=='mode_2':self.assertEqual({'W','G'},set(s.engine._effective_card_data(token)['colors']))
                self.assertEqual(0,s.state.cards[second.object_id].marked_damage)
                self.finish(s,private)

    def test_clash_offers_legal_counter_mode_without_legal_fight_and_removal_does_not_retrigger(self):
        extra={}
        def before(s,spell,first,second):
            extra['saga']=self.add(s.engine,'History of Benalia');extra['saga'].counters['lore']=2
            s.engine.move_card(second.object_id,'graveyard',log=False)
        s,spell,first,second,private=self.scene('Clash of the Eikons',before=before)
        row=self.action(s,'A',spell)
        self.assertEqual(['mode_2','mode_3'],row['target_schema']['legal_modes'])
        self.reject(s,'A',{'action_id':row['id'],'modes':['mode_1'],'targets':[first.ref,second.ref],'pay':'auto'})
        self.cast_modes(s,spell,['mode_2'],[extra['saga'].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[extra['saga'].object_id].counters['lore'])
        self.assertFalse(any(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        self.finish(s,private)

    def test_partial_target_illegality_keeps_untargeted_mode_but_all_illegal_targets_stop_the_spell(self):
        for all_illegal in (False,True):
            with self.subTest(all_illegal=all_illegal):
                extra={}
                def before(s,spell,first,second):
                    extra['other']=self.add(s.engine,'Grizzly Bears',ref='unaffected-other')
                    extra['bounce1']=self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='bounce-recipient')
                    extra['bounce2']=self.add(s.engine,'Unsummon',seat='B',zone='hand',ref='bounce-source')
                    s.state.players['B'].mana_pool['U']=2
                s,spell,first,second,private=self.scene('Markov Retribution',fighter='Vampire Nighthawk',before=before)
                self.cast_modes(s,spell,['mode_1','mode_2'],[first.ref,second.ref])
                self.cast(s,'B',extra['bounce1'],[second.ref])
                if all_illegal:self.cast(s,'B',extra['bounce2'],[first.ref])
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('hand',s.state.cards[second.object_id].zone)
                self.assertEqual('hand' if all_illegal else 'battlefield',s.state.cards[first.object_id].zone)
                self.assertEqual(2 if all_illegal else 3,s.engine._numeric_stat(extra['other'].object_id,'power'))
                self.assertEqual(40,s.state.players['A'].life)
                self.finish(s,private)

    def test_opponent_copy_keeps_both_modes_rebinds_roles_and_preserves_target_slot_counts(self):
        extra={}
        def before(s,spell,first,second):
            extra['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand')
            extra['fighter']=self.add(s.engine,'Vampire Nighthawk',seat='B',ref='copied-fighter-B')
            extra['opponent']=self.add(s.engine,'Hill Giant',ref='copy-target-A')
            extra['wrong']=self.add(s.engine,'Grizzly Bears',ref='new-illegal-fighter-A')
            s.state.players['B'].mana_pool['R']=2
        s,spell,first,second,private=self.scene('Epic Fight',before=before)
        self.cast_modes(s,spell,['mode_2','mode_1'],[first.ref,first.ref,second.ref])
        self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        command={'action_id':'choose','targets':[extra['fighter'].ref,extra['fighter'].ref,extra['opponent'].ref]}
        self.reject(s,'A',command)
        self.reject(s,'B',{**command,'targets':command['targets'][:-1]})
        self.reject(s,'B',{**command,'targets':[extra['wrong'].ref,extra['wrong'].ref,extra['opponent'].ref]})
        s=self.replay(s,load=True);result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(('B',['mode_1','mode_2']),(s.state.stack[-1].controller,s.state.stack[-1].modes))
        self.resolve_top(s)
        self.assertEqual('graveyard',s.state.cards[extra['opponent'].object_id].zone)
        self.assertEqual(3,s.state.cards[extra['fighter'].object_id].marked_damage)
        self.assertEqual(44,s.state.players['B'].life)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[second.object_id].zone)
        self.assertEqual(4,s.engine._numeric_stat(first.object_id,'power'))
        self.finish(s,private)

    def test_illegal_brigid_copy_target_does_not_copy_a_returned_incarnation_or_block_another_mode(self):
        extra={}
        def before(s,spell,first,second):
            extra['blink']=self.add(s.engine,'Cloudshift',zone='hand')
            extra['pump']=self.add(s.engine,'Grizzly Bears',ref='independent-pump')
        s,spell,first,second,private=self.scene("Brigid's Command",fighter='Goldmeadow Harrier',before=before)
        old_identity=first.logical_object_id
        self.cast_modes(s,spell,['mode_1','mode_3'],[first.ref,extra['pump'].ref])
        self.cast(s,'A',extra['blink'],[first.ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old_identity,s.state.cards[first.object_id].logical_object_id)
        self.assertFalse(any(c.is_token and c.zone=='battlefield' for c in s.state.cards.values()))
        self.assertEqual(5,s.engine._numeric_stat(extra['pump'].object_id,'power'))
        self.finish(s,private)

    def test_executable_grouped_modes_require_modal_and_child_owners_without_coverage_annotations(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,spell,first,second,private=self.scene('Epic Fight')
        program=next(p for p in s.engine.semantics.programs_for_oracle(spell.oracle_id) if p.target_schema and 'modes' in p.target_schema)
        for cap in ('choice.modal.fixed_nonrepeating','damage.creature_power.fixed_interaction','continuous.resolution.fixed_stat_multiplier'):
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_fresh_shared_fighter_witness_kills_accidental_cross_mode_distinctness(self):
        from unittest.mock import patch
        import quorune.selection.targeting as owner
        method='test_originals_execute_grouped_modes_after_their_printed_preparation'
        getattr(type(self)(method),method)()
        original=owner.target_plan
        def incorrect(*args,**kwargs):
            plan=original(*args,**kwargs)
            return replace(plan,globally_distinct=True) if len(plan.modes)>1 else plan
        with patch.object(owner,'target_plan',side_effect=incorrect):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
