"""Fixed Mutate metadata is not casting or complete-card admission."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict,canonical_ability_fragments
from quorune.carddb import CardDatabase
from quorune.card_programs.runtime_requirements import _event_query_runtime_requirements
from quorune.mutate_model import MutateSpec,MUTATE_CAPABILITY,MUTATE_HANDLER
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.keyword_runtime_requirements import keyword_runtime_dependencies
from quorune.semantic_runtime.ability_fragments import default_ability_fragment_registry
from quorune.semantic_runtime.mutate import MutateAbilityHandler
from quorune.semantics import SemanticProgram
from quorune.targets import target_plan
from quorune.rules.casting.mutate_costs import compiled_mutate_specs,with_mutate_cost_schema
from quorune.rules.mutate_resolution import mutating_spell_spec
from quorune.semantic_choices.mutate import MutateMergeChoiceHandler
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery,SemanticChoiceContext
from quorune.semantic_choices.model import SemanticChoiceContinuation,SemanticChoiceFrame,SemanticChoiceError
from quorune.object_query import ObjectQueryResult
from quorune.model import StackItem,CardInstance,GameConfig,GameState
from quorune.permanent_composition import MergePosition
from quorune.errors import GameRuleError
from quorune.compiler.mutate_event_bindings import mutate_event_binding_spec
from quorune.selection.targeting import TargetSelectionOwnerMixin
from scripts.build_test_database import build_fixture_database

FIXTURE=ROOT/'tests/fixtures/mutate-casting-cards.json'


class MutateCastMetadataTests(unittest.TestCase):
    def test_fixed_cost_codec_and_hybrid_choices_share_the_existing_mana_expander(self):
        spec=MutateSpec('{2}{U/R}{U/R}')
        self.assertEqual(spec,MutateSpec.from_dict(spec.to_dict()))
        choices={(r['GENERIC'],r['U'],r['R']) for r in spec.mana_variants}
        self.assertEqual({(2,2,0),(2,1,1),(2,0,2)},choices)
        fragment=ability_fragment_to_dict(spec)
        self.assertEqual({'kind':'mutate','value':{'schema_version':1,'mana_cost':'{2}{U/R}{U/R}'}},fragment)
        self.assertEqual(spec,ability_fragment_from_dict(fragment));self.assertEqual((spec,),canonical_ability_fragments((spec,)))
        group=target_plan(spec.target_schema()).groups[0]
        self.assertEqual('spell_owner',group.owner_relation);self.assertEqual(('human',),group.subtypes_none)
        options=spec.cast_cost_options()
        self.assertEqual(3,len(options));self.assertEqual(3,len({r['id'] for r in options}))
        self.assertEqual(choices,{(r['requirements']['GENERIC'],r['requirements']['U'],r['requirements']['R']) for r in options})
        for option in options:
            self.assertEqual(spec.to_dict(),option['mutate_payment'])
            self.assertEqual(spec.target_schema(),option['target_schema'])
            self.assertEqual([{'op':'mutate_merge','target':'$target.0'}],option['effects'])
        for bad in ('','{X}','{T}','{U/P}','{2/W}','{Z}','{G} Draw a card.',True):
            with self.subTest(bad=bad),self.assertRaises(ValueError):MutateSpec(bad)
        for change in ({'schema_version':True},{'schema_version':2},{'owner':'A'},{'controller':'B'}):
            with self.subTest(change=change),self.assertRaises(ValueError):MutateSpec.from_dict({**spec.to_dict(),**change})

    def test_existing_registry_validates_copiable_metadata_and_infers_its_owner(self):
        spec=MutateSpec('{1}{U}');fragment=ability_fragment_to_dict(spec)
        value={'handler_id':MUTATE_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
            'fragment':fragment,'requires_complete_card_program':True}
        handler=MutateAbilityHandler();self.assertEqual(spec,handler.validate(value))
        self.assertEqual((spec,),handler.lower(value,None))
        default_ability_fragment_registry().validate(value)
        program=SemanticProgram(key='fixture:mutate-dependency',label='Mutate dependency',event='characteristics.evaluate',
            effects=[],handlers=[value],capability_dependencies=[])
        self.assertIn(MUTATE_CAPABILITY,keyword_runtime_dependencies(program))
        for change in ({'schema_version':True},{'event':'cast.cost'},{'requires_complete_card_program':False},
                {'fragment':{'kind':'enlist','value':{'schema_version':1}}},{'extra':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):handler.validate({**value,**change})

    def test_alternative_cost_owner_requires_current_complete_face_bound_source(self):
        spec=MutateSpec('{2}{G}')
        descriptor={'handler_id':MUTATE_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
            'fragment':ability_fragment_to_dict(spec),'requires_complete_card_program':True}
        admission={'schema_version':1,'oracle_ir_status':'exact','material_residual_count':0}
        program=SemanticProgram(key='fixture:mutate-cost-owner',label='Cost-owner contract',
            event='characteristics.evaluate',effects=[],handlers=[descriptor],
            provenance={'face_id':'front','card_program_admission':admission})
        record=SimpleNamespace(oracle_id='fixture:mutate-cost',faces=())
        card=SimpleNamespace(face_down=False,annotations={},active_face=None)
        host=SimpleNamespace(card_record=lambda _:record,semantic_program_is_current_trusted=lambda _:True,
            semantics=SimpleNamespace(runtime_handler_programs_for_oracle=lambda *args,**kwargs:(program,)))
        self.assertEqual((spec,),compiled_mutate_specs(host,card))
        schema={'optional_costs':[{'id':'existing'}]}
        augmented=with_mutate_cost_schema(host,card,schema,suppress_source_costs=False)
        self.assertEqual([spec.cast_cost_options()[0]],augmented.get('alternate_costs',[]))
        self.assertNotIn('alternate_costs',schema)
        self.assertEqual(schema,with_mutate_cost_schema(host,card,schema,suppress_source_costs=True))
        self.assertIsNone(with_mutate_cost_schema(host,card,augmented,suppress_source_costs=False))
        for change in ({'face_id':'back'},{'card_program_admission':{**admission,'material_residual_count':1}},
                {'card_program_admission':{**admission,'oracle_ir_status':'partial'}},{'card_program_admission':{}}):
            with self.subTest(change=change):
                original=program.provenance;program.provenance={**original,**change}
                self.assertEqual((),compiled_mutate_specs(host,card));program.provenance=original
        host.semantic_program_is_current_trusted=lambda _:False
        self.assertEqual((),compiled_mutate_specs(host,card))
        self.assertEqual(schema,with_mutate_cost_schema(host,card,schema,suppress_source_costs=False))
        host.semantic_program_is_current_trusted=lambda _:True
        card.annotations={'copy_overrides':{}}
        self.assertEqual((),compiled_mutate_specs(host,card))

    def test_every_pinned_mutate_cost_lowers_but_blocked_integration_never_admits_it(self):
        records=json.loads(FIXTURE.read_text(encoding="utf-8"))['cards'];originals=[r for r in records if 'Mutate' in r['keywords']]
        self.assertEqual(34,len(originals))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(row for row in raw['capabilities'] if row['id']==MUTATE_CAPABILITY).update(
            status='blocked',blockers=['Mutate executable authority withdrawn'])
        registry=CapabilityRegistry(raw)
        with tempfile.TemporaryDirectory() as folder:
            build_fixture_database([FIXTURE],Path(folder)/'mutate.sqlite3')
            with CardDatabase(Path(folder)/'mutate.sqlite3') as db:
                for row in originals:
                    with self.subTest(name=row['name']):
                        ir=compile_oracle_card(db.by_oracle_id(row['oracle_id']),capability_registry=registry,capability_profile='commander_review')
                        nodes=[n for face in ir.faces for n in face.nodes if n.template_id=='fixed-mana-mutate-cost-v1']
                        self.assertEqual(1,len(nodes));self.assertTrue(nodes[0].lowerable);self.assertFalse(nodes[0].exact)
                        self.assertEqual('all',nodes[0].active_zone)
                        self.assertEqual(MUTATE_HANDLER,nodes[0].handlers[0]['handler_id'])
                        self.assertNotEqual('exact',ir.status,'Metadata must not substitute for casting/resolution authority')


class MutateResolutionOwnerTests(unittest.TestCase):
    def test_source_counter_pronoun_uses_existing_public_source_counter_owner(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.event_counter_results import PUBLIC_SOURCE_COUNTER_CAPABILITY
        text='Whenever this creature mutates, put two +1/+1 counters on it.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        self.assertEqual('$source.zone_object',node.effects[0]['card'])
        self.assertEqual(2,node.effects[0]['amount'])
        self.assertIn(PUBLIC_SOURCE_COUNTER_CAPABILITY,node.capability_dependencies)
        other=compile_oracle_card(query_record('Whenever another creature you control enters, put a +1/+1 counter on it.'),
            capability_registry=load_default_capability_registry())
        self.assertEqual('exact',other.status,other.material_residuals)
        self.assertNotEqual('$source.zone_object',other.faces[0].nodes[0].effects[0]['card'])

    def test_omitted_source_counter_pronoun_normalization_is_killed(self):
        import quorune.compiler.event_counter_results as owner
        original=owner.public_event_source_counter_result
        def previous(binding,body,**values):
            values['allow_source_pronoun']=False
            return original(binding,body,**values)
        self.test_source_counter_pronoun_uses_existing_public_source_counter_owner()
        with patch.object(owner,'public_event_source_counter_result',side_effect=previous),self.assertRaises(AssertionError):
            self.test_source_counter_pronoun_uses_existing_public_source_counter_owner()

    def test_detached_spell_provenance_uses_only_its_captured_physical_source(self):
        from quorune.record_trust import (semantic_execution_provenance_row,
            capture_stack_resolution_inputs,resolved_semantic_execution_provenance_rows)
        from quorune.stack_resolution import physical_stack_record
        from quorune.errors import StateInvariantError
        state=GameState(game_id='detached-source',config=GameConfig(),players={},cards={},
            deck_names={},commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)
        card=CardInstance('physical-source','O1','fixture:physical-source','Physical source','A','A','stack')
        record=SimpleNamespace(oracle_id=card.oracle_id,faces=())
        def card_record(value):
            if value is card:return record
            raise KeyError(value)
        host=SimpleNamespace(state=state,card_record=card_record,card_db=SimpleNamespace(by_oracle_id=lambda _:record),
            semantics=SimpleNamespace(get=lambda _:None),_trusted_generic_spell=lambda value:value is record)
        item=StackItem('stack-object','S1','spell','A','Physical source',card_object_id=card.object_id,
            semantic_key=card.oracle_id+':spell:front',default_destination='battlefield')
        row=semantic_execution_provenance_row(host,item.semantic_key,capability_registry=None,
            profile='commander_review',stack_item=item,physical_card=card)
        self.assertEqual(card.oracle_id,row['oracle_id'])
        self.assertEqual('trusted_generic_permanent_spell',row['implicit_fallback'])
        self.assertFalse(state.cards,'Provenance must not recreate a detached live object')
        with self.assertRaises(StateInvariantError):
            physical_stack_record(host,item,physical_card=replace(card,object_id='another-physical-source'))
        state.cards[card.object_id]=card
        host.card_record=lambda value:record if value==card.object_id else self.fail('Live lookup changed its existing reference contract')
        self.assertIs(record,physical_stack_record(host,item,physical_card=card))
        state.stack.append(item)
        captured=capture_stack_resolution_inputs(state)
        self.assertEqual((item.ref,),tuple(captured));self.assertEqual(1,len(captured))
        self.assertIs(item,captured.get(item.ref))
        with self.assertRaises(TypeError):captured.stack_items['another']=item
        state.cards.clear();state.stack.clear();host.card_record=card_record
        events=[SimpleNamespace(event_id=1,code='stack.resolve',details={'stack':item.ref})]
        rows=resolved_semantic_execution_provenance_rows(host,events=events,before_event_sequence=0,
            before_stack_items=captured,capability_profile='commander_review')
        self.assertEqual(card.oracle_id,rows[0]['oracle_id']);self.assertFalse(state.cards)

    def test_cost_owner_target_owner_and_fallback_omission_mutants_are_killed(self):
        from test_spell_owner_targeting import SpellOwnerTargetingTests
        import quorune.rules.spell_owner_targets as target_owner
        def killed(case):
            result=unittest.TestResult();case.run(result)
            self.assertFalse(result.errors,result.errors)
            self.assertTrue(result.failures,'The focused production mutation survived')
        self.test_shared_revalidation_keeps_mutate_creature_fallback_and_generic_fizzle_distinct()
        with patch('quorune.rules.mutate_resolution.mutating_spell_spec',return_value=None):
            killed(MutateResolutionOwnerTests('test_shared_revalidation_keeps_mutate_creature_fallback_and_generic_fizzle_distinct'))
        costs=MutateCastMetadataTests('test_alternative_cost_owner_requires_current_complete_face_bound_source')
        costs.test_alternative_cost_owner_requires_current_complete_face_bound_source()
        with patch('quorune.rules.casting.mutate_costs.compiled_mutate_specs',return_value=()):
            killed(MutateCastMetadataTests('test_alternative_cost_owner_requires_current_complete_face_bound_source'))
        targets=SpellOwnerTargetingTests('test_shared_matcher_uses_spell_owner_across_target_controllers')
        targets.test_shared_matcher_uses_spell_owner_across_target_controllers()
        def controller_instead_of_owner(source,owner,category):
            return source is not None and source.source_controller==owner
        with patch.object(target_owner,'target_has_spell_owner',side_effect=controller_instead_of_owner):
            killed(SpellOwnerTargetingTests('test_shared_matcher_uses_spell_owner_across_target_controllers'))

    def test_shared_revalidation_keeps_mutate_creature_fallback_and_generic_fizzle_distinct(self):
        spec=MutateSpec('{2}{G}');option=spec.cast_cost_options()[0]
        group=target_plan(option['target_schema']).groups[0].group_id
        for mutate in (False,True):
            with self.subTest(mutate=mutate):
                countered=[];priority=[];logs=[]
                host=SimpleNamespace(state=SimpleNamespace(cards={},active_player='A'),
                    semantics=SimpleNamespace(get=lambda _:None),
                    _stack_target_schema=lambda *_:spec.target_schema(),_stack_source_ref=lambda _:None,
                    _target_candidate_map=lambda *args,**kwargs:{group:[]},
                    _target_identity_matches_snapshot=lambda *args:False,
                    _increment_optimization=lambda *args:None,_log=lambda *args,**kwargs:logs.append(args[1]),
                    _counter_stack_item=lambda *args,**kwargs:countered.append(args[0]),
                    _grant_priority=priority.append)
                context={'target_groups':{group:['O1']},'target_snapshots':{'O1':{}}}
                if mutate:context.update({'mutate_payment':spec.to_dict(),'cost_option':option['id'],
                    'target_schema_override':option['target_schema'],'cast_option_effects':option['effects']})
                item=StackItem(stack_id='resolution-spell',ref='S1',kind='spell',controller='B',
                    label='Target revalidation owner',targets=['O1'],context=context)
                result=TargetSelectionOwnerMixin._revalidate_resolution_targets(host,item)
                self.assertEqual(mutate,result);self.assertEqual([None],item.targets)
                self.assertEqual([] if mutate else ['S1'],countered)
                self.assertEqual([] if mutate else ['A'],priority)
                self.assertEqual(mutate,'mutate.target.illegal' in logs)

    def test_mutation_event_is_source_specific_and_retains_inferred_capability(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        for subject in ('this creature','Generic Query Observer'):
            binding=fixed_counter_trigger_binding('Whenever '+subject+' mutates, draw a card.',card_name='Generic Query Observer')
            self.assertEqual('permanent.mutated.self',binding.event.value)
            self.assertIn(MUTATE_CAPABILITY,binding.public_capabilities)
        for text in ('Whenever a creature mutates, draw a card.',
                'Whenever this creature attacks or mutates, draw a card.',
                'Whenever another creature mutates, draw a card.'):
            self.assertIsNone(mutate_event_binding_spec(text,card_name='Generic Query Observer'))
        program=SemanticProgram(key='fixture:mutation-event',label='Mutation event',
            event='permanent.mutated.self',active_zone='battlefield',effects=[],coverage=[],capability_dependencies=[])
        required,blockers=_event_query_runtime_requirements(program)
        self.assertIn(MUTATE_CAPABILITY,required);self.assertFalse(blockers)

    def _choice(self):
        source=ObjectQueryResult('spell','S1','Mutating spell','A','B','stack',types=('creature',),logical_object_id='spell@2')
        root=ObjectQueryResult('root','O1','Retained creature','A','C','battlefield',types=('creature',),logical_object_id='root@1')
        query=SnapshotSemanticChoiceQuery(('A','B','C','D'),('A','B','C','D'),object_rows=(source,root))
        context=SemanticChoiceContext('B','S1','B','Mutating spell','O2','O2','fixture:mutate',1,query)
        handler=MutateMergeChoiceHandler()
        prepared=handler.prepare({'op':'mutate_merge','target':'O1'},context)
        continuation=SemanticChoiceContinuation(handler.handler_id,1,'S1',prepared.continuation_effect,(),
            'battlefield','',SemanticChoiceFrame('fixture:mutate',1,'S1',0,'B'))
        return handler,context,prepared,continuation,source,root

    def test_spell_controller_chooses_both_positions_and_choice_survives_its_codec(self):
        handler,context,prepared,continuation,source,root=self._choice()
        self.assertEqual(('top','bottom'),prepared.request.choice.legal_values)
        saved=SemanticChoiceContinuation.from_dict(continuation.to_dict())
        for position in ('top','bottom'):
            with self.subTest(position=position):
                complete=handler.complete(saved,{'action':'choose','choice':position},context.query)
                self.assertEqual(1,len(complete.intents));intent=complete.intents[0]
                self.assertEqual('B',intent.actor);self.assertEqual(MergePosition(position),intent.position)
                self.assertEqual('root@1',intent.target.logical_object_id)
                self.assertEqual('spell@2',intent.source.logical_object_id)
        with self.assertRaises(SemanticChoiceError):
            handler.prepare({'op':'mutate_merge','target':'O1'},replace(context,actor='C'))

    def test_stale_objects_ownership_and_malformed_choices_reject(self):
        handler,context,prepared,continuation,source,root=self._choice()
        for response in ({'choice':'middle'},{'choice':True},{'choice':'top','extra':True},
                {'choice':'top','action':'pass'},{'choice':'top','choice_schema':True},
                {'choice':'top','choice_schema':{'legal_values':['top']}}):
            with self.subTest(response=response),self.assertRaises(SemanticChoiceError):
                handler.complete(continuation,response,context.query)
        for changed in (replace(root,owner='B'),replace(root,subtypes=('human',)),replace(root,phased_out=True),
                replace(root,logical_object_id='root@2'),replace(root,object_id='replacement')):
            with self.subTest(root=changed),self.assertRaises(SemanticChoiceError):
                handler.complete(continuation,{'choice':'bottom'},replace(context.query,object_rows=(source,changed)))
        with self.assertRaises(SemanticChoiceError):
            handler.complete(replace(continuation,remaining=({'op':'draw','player':'B'},)),{'choice':'top'},context.query)

    def test_only_a_closed_announced_alternative_can_use_the_illegal_target_exception(self):
        spec=MutateSpec('{2}{U/R}');option=spec.cast_cost_options()[0]
        context={'mutate_payment':spec.to_dict(),'cost_option':option['id'],
            'target_schema_override':option['target_schema'],'cast_option_effects':option['effects']}
        item=StackItem(stack_id='spell-stack-id',ref='S1',kind='spell',controller='B',label='Mutating spell',
            targets=['O1'],context=context)
        self.assertEqual(spec,mutating_spell_spec(item))
        self.assertIsNone(mutating_spell_spec(replace(item,context={})))
        for changes in ({'cost_option':'printed'},{'target_schema_override':{'count':1}},
                {'cast_option_effects':[]},{'mutate_payment':{'schema_version':True,'mana_cost':'{G}'}}):
            with self.subTest(changes=changes),self.assertRaises(GameRuleError):
                mutating_spell_spec(replace(item,context={**context,**changes}))
        with self.assertRaises(GameRuleError):mutating_spell_spec(replace(item,kind='trigger'))
        with self.assertRaises(GameRuleError):mutating_spell_spec(replace(item,targets=[]))


if __name__=='__main__':unittest.main()
