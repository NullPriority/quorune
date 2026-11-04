"""CR601.2c/115.7d: a player supplies at most one chosen permanent."""

import unittest
from types import SimpleNamespace

from quorune.errors import GameRuleError
from quorune.replacement.immutable import FrozenMap
from quorune.targets import target_plan
from quorune.selection.target_validation import CopyTargetValidation,validate_grouped_target_assignment


class PerPlayerTargetSetOwnerTests(unittest.TestCase):
    def test_distinct_controller_validation_omission_is_killed(self):
        from unittest.mock import patch
        with patch('quorune.selection.target_validation._distinct_target_controllers',return_value=None):
            result=unittest.TestResult()
            PerPlayerTargetSetOwnerTests('test_optional_set_accepts_zero_and_distinct_players_but_rejects_two_from_one_player').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)

    def test_verified_per_player_withdrawal_preserves_ordinary_announced_sets(self):
        import json
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        text='For each opponent, destroy up to one target artifact or enchantment that player controls.'
        self.assertEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='target.set.one_per_player').update(status='blocked',blockers=['Per-player target authority withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('Destroy up to two target artifacts.','Sorcery'),capability_registry=registry).status)

    def test_compiler_lowering_uses_existing_announced_sets_and_infers_fine_authority(self):
        from quorune.compiler.per_player_target_sets import per_player_target_set_template
        from quorune.rules.per_player_target_sets import per_player_target_node_capabilities,per_player_target_runtime_dependencies
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.rules.fixed_homogeneous_target_set_capability_shapes import announced_target_runtime_dependencies
        for text,relation in (('For each opponent, tap up to one target creature that player controls.','opponent'),
                ('For each player, destroy up to one target artifact that player controls.','any')):
            compiled=per_player_target_set_template(text);self.assertIsNotNone(compiled)
            _name,effects,schema,mechanics=compiled
            self.assertEqual(relation,schema.get('controller_relation','any'))
            self.assertEqual('any_number',schema['count_parameter']['parameter'])
            self.assertIn('target.set.one_per_player',per_player_target_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics))
            self.assertIn('target.set.one_per_player',capability_dependencies_for_node(effects=effects,target_schema=schema,mechanic_ids=mechanics))
            program=SimpleNamespace(effects=effects,target_schema=schema,coverage=())
            self.assertIn('target.set.one_per_player',per_player_target_runtime_dependencies(program))
            self.assertIn('target.set.one_per_player',announced_target_runtime_dependencies(SimpleNamespace(**{**vars(program),'coverage':mechanics})))
            for change in ({'distinct_controllers':False},{'retain_controller':False},{'controller_relation':'you'},{'up_to':6}):
                with self.subTest(change=change),self.assertRaises(ValueError):
                    per_player_target_runtime_dependencies(SimpleNamespace(**{**vars(program),'target_schema':{**schema,**change}}))
                self.assertEqual((),capability_dependencies_for_node(effects=effects,target_schema={**schema,**change},mechanic_ids=mechanics))
            ordinary={key:value for key,value in schema.items() if key not in {'distinct_controllers','retain_controller'}}
            self.assertEqual((),capability_dependencies_for_node(effects=effects,target_schema=ordinary,mechanic_ids=mechanics))
        for text in ('For each opponent, tap target creature that player controls.',
                'For each opponent, tap up to two target creatures that player controls.',
                'For each player, tap up to one target creature that opponent controls.',
                'For each opponent, tap up to one target creature that player controls. Rewrite history.'):
            self.assertIsNone(per_player_target_set_template(text))

    def test_controller_sensitive_cost_witnesses_keep_a_representative_for_each_player(self):
        from quorune.selection.target_validation import target_assignment_witnesses
        plan=target_plan(self.schema());candidates={'target_0':['B1','B2','C1','D1']}
        witnesses=list(target_assignment_witnesses(self.host(),plan,candidates,
            extra_targets=2,eligible_sets=(set(candidates['target_0']),)))
        selected={tuple(refs) for refs,_groups in witnesses}
        self.assertIn(('B1','C1'),selected)
        self.assertNotIn(('B1','B2'),selected)

    def schema(self):
        return {'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],
            'up_to':6,'controller_relation':'opponent','distinct_controllers':True,'retain_controller':True}

    def host(self,**changes):
        rows={ref:{'ref':ref,'object_id':ref,'zone_change_counter':0,'zone':'battlefield',
            'category':'permanent','controller':controller,'owner':controller}
            for ref,controller in (('B1','B'),('B2','B'),('C1','C'),('D1','D'))}
        for ref,values in changes.items():rows[ref].update(values)
        return SimpleNamespace(_target_snapshot=lambda ref:rows.get(ref,{'ref':ref}))

    def test_optional_set_accepts_zero_and_distinct_players_but_rejects_two_from_one_player(self):
        plan=target_plan(self.schema());host=self.host();candidates={'target_0':['B1','B2','C1','D1']}
        self.assertTrue(plan.groups[0].distinct_controllers)
        self.assertTrue(plan.groups[0].retain_controller)
        for refs in ([],['B1'],['B1','C1','D1']):
            self.assertEqual(refs,validate_grouped_target_assignment(host,plan,{'target_0':refs},candidates))
        with self.assertRaises(GameRuleError):
            validate_grouped_target_assignment(host,plan,{'target_0':['B1','B2']},candidates)

    def test_copy_keeps_count_and_retained_originals_but_new_targets_obey_controller_relation(self):
        plan=target_plan(self.schema());host=self.host();original={'target_0':['B1','C1']}
        proof=CopyTargetValidation(FrozenMap({'target_0':2}),FrozenMap(original),
            FrozenMap({ref:host._target_snapshot(ref) for ref in original['target_0']}))
        plan=proof.fixed_plan(plan)
        self.assertEqual(['B1','C1'],validate_grouped_target_assignment(self.host(B1={'controller':'C'}),
            plan,original,{'target_0':['C1','D1']},copy_targets=proof))
        partial=CopyTargetValidation(proof.original_counts,FrozenMap({'target_0':['C1']}),
            FrozenMap({'C1':host._target_snapshot('C1')}))
        self.assertEqual(['D1','C1'],validate_grouped_target_assignment(host,plan,{'target_0':['D1','C1']},
            {'target_0':['B1','B2','C1','D1']},copy_targets=partial))
        with self.assertRaises(GameRuleError):
            validate_grouped_target_assignment(self.host(D1={'controller':'C'}),plan,
                {'target_0':['D1','C1']},{'target_0':['C1','D1']},copy_targets=partial)
        with self.assertRaises(GameRuleError):
            validate_grouped_target_assignment(host,plan,{'target_0':['D1']},
                {'target_0':['D1']},copy_targets=partial)

    def test_recorded_controller_revalidation_and_strict_flag_types(self):
        from quorune.selection.target_validation import target_controller_matches_snapshot
        schema=self.schema();group=target_plan(schema).groups[0];host=self.host();snapshot=host._target_snapshot('B1')
        self.assertTrue(target_controller_matches_snapshot(host,group,'B1',snapshot))
        self.assertFalse(target_controller_matches_snapshot(self.host(B1={'controller':'C'}),group,'B1',snapshot))
        self.assertFalse(target_controller_matches_snapshot(host,group,'B1',{}))
        for field in ('distinct_controllers','retain_controller'):
            for value in (1,'true',None,[]):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):target_plan({**schema,field:value})
