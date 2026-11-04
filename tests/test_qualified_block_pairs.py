"""CR509.3: blocking counterpart qualities are frozen for the actual pair."""
from copy import deepcopy
from types import SimpleNamespace
import json,unittest
from unittest.mock import patch
from common import ROOT
from quorune.block_characteristic_model import (BLOCK_CHARACTERISTIC_CONTEXT,
    block_counterpart_query,block_characteristics_version)
from quorune.compiler.qualified_block_pairs import qualified_block_pair_spec
from quorune.object_query import ObjectQueryResult
from quorune.rules.block_characteristics import block_characteristic_contexts,qualified_block_runtime_dependencies,block_counterpart_matches


def facts(**changes):
    return {'schema_version':1,'object_id':'other-id','reference':'other','logical_object_id':'other@0','owner':'B','controller':'B',
        'power':2,'toughness':3,'types':['creature'],'subtypes':['bear'],'supertypes':[],'colors':['G'],'keywords':[],**changes}


def qualifier(spec):
    condition=spec.condition
    if 'event_subscriptions'in condition:condition=condition['event_subscriptions'][0]['condition']
    return next(row for row in condition['all'] if row.get('field')=='block_counterpart_matches')


def pair_context(snapshot):
    from quorune.block_transitions import BlockTransitionEvent,BlockTransitionParticipant,BlockTransitionAssignment
    transition=BlockTransitionEvent.create(turn_sequence=1,priority_epoch=2,active_player='B',participants=(
        BlockTransitionParticipant('other-id','other@0','other','B'),
        BlockTransitionParticipant('source-id','source@0','source','A')),
        assignments=(BlockTransitionAssignment('other-id','source-id'),))
    return {'block_pair_event':'creature.blocks','card':'source','blocked_attacker':'other',
        'block_transition':transition.to_dict(),BLOCK_CHARACTERISTIC_CONTEXT:snapshot}


class QualifiedBlockPairOwnerTests(unittest.TestCase):
    def test_single_blocked_by_counterpart_body_uses_pair_event_and_preserves_source_only_binding(self):
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        for body in ('destroy that creature.','this creature deals 1 damage to that creature.'):
            binding=fixed_counter_trigger_binding('Whenever this creature becomes blocked by a creature, '+body,card_name='Source')
            self.assertEqual('source_block_pair_counterpart',binding.variant)
            self.assertEqual('creature.becomes_blocked_by',binding.event.value)
        source=fixed_counter_trigger_binding('Whenever this creature becomes blocked by a creature, this creature gets +1/+1 until end of turn.',card_name='Source')
        self.assertEqual('this_creature_becomes_blocked_by_creature',source.variant)
    def test_qualified_pair_compilation_preserves_single_union_scope_and_rejects_aggregation(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for head,body in (('blocks or becomes blocked by a nonblack creature','destroy that creature at end of combat.'),
                ('blocks or becomes blocked by a non-Wall creature','destroy that creature at end of combat.'),
                ('blocks or becomes blocked by a green or white creature','destroy that creature at end of combat.'),
                ('becomes blocked by an artifact creature','destroy that creature.'),
                ('becomes blocked by a green creature',"destroy that creature. It can't be regenerated."),
                ('blocks a creature',"return that creature to its owner's hand at end of combat."),
                ('blocks or becomes blocked by a white creature','this creature gains first strike until end of turn.')):
            with self.subTest(head=head):
                ir=compile_oracle_card(record('Whenever this creature '+head+', '+body,'Creature — Beast'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIsNone(ir.faces[0].nodes[0].target_schema)
        for head in ('blocks or becomes blocked by one or more black creatures','blocks or becomes blocked by a creature that shares a color with it'):
            self.assertIsNone(qualified_block_pair_spec('Whenever this creature '+head+', destroy that creature at end of combat.'))

    def test_verified_block_qualifier_withdrawal_keeps_ordinary_unqualified_pairs(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']=='trigger.event.block_counterpart_characteristics').update(status='blocked',blockers=['block qualifier authority withdrawn'])
        registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record('Whenever this creature blocks or becomes blocked by a nonblack creature, destroy that creature at end of combat.','Creature — Beast'),capability_registry=registry).status)
        plain=compile_oracle_card(record('Whenever this creature blocks or becomes blocked by a creature, destroy that creature at end of combat.','Creature — Beast'),capability_registry=registry)
        self.assertEqual('exact',plain.status,plain.material_residuals)

    def test_sealed_counterpart_queries_keep_color_type_subtype_numeric_and_exact_pair_identity(self):
        cases=(('a nonblack creature',facts(),True),('a nonblack creature',facts(colors=['B']),False),
            ('a non-Wall creature',facts(),True),('a non-Wall creature',facts(subtypes=['wall']),False),
            ('a green or white creature',facts(colors=['W']),True),('a green or white creature',facts(colors=['R']),False),
            ('an artifact creature',facts(types=['artifact','creature']),True),('an artifact creature',facts(),False),
            ('a Vampire',facts(subtypes=['vampire']),True),('a Vampire',facts(),False),
            ('a creature with power 1 or less',facts(power=1),True),('a creature with power 1 or less',facts(power=2),False))
        for subject,snapshot,expected in cases:
            with self.subTest(subject=subject,snapshot=snapshot):
                spec=qualified_block_pair_spec('Whenever this creature blocks or becomes blocked by '+subject+', destroy that creature at end of combat.')
                self.assertIsNotNone(spec)
                context=pair_context(snapshot)
                self.assertEqual(expected,block_counterpart_matches(qualifier(spec),context))
                with self.assertRaises(ValueError):block_counterpart_matches(qualifier(spec),{**context,'blocked_attacker':'unrelated'})
                self.assertFalse(block_counterpart_matches(qualifier(spec),{'block_pair_event':'creature.blocks','blocked_attacker':'other'}))

    def test_fact_capture_is_immutable_and_historical_absence_keeps_the_old_event_shape(self):
        card=SimpleNamespace(logical_object_id='other@0')
        participant=SimpleNamespace(object_id='other-id',logical_object_id='other@0')
        row=ObjectQueryResult('other-id','other','Witness','B','B','battlefield',types=('creature',),colors=('G',),
            subtypes=('bear',),effective_power=2,effective_toughness=3,logical_object_id='other@0')
        host=SimpleNamespace(state=SimpleNamespace(block_characteristics_version=1,cards={'other-id':card}),_public_object_query_result=lambda card:row)
        captured=block_characteristic_contexts(host,SimpleNamespace(participants=(participant,)))
        row=ObjectQueryResult('other-id','other','Witness','B','B','battlefield',types=('creature',),colors=('B',),logical_object_id='other@0')
        self.assertEqual(['G'],captured['other-id']['colors'])
        host.state.block_characteristics_version=None
        self.assertEqual({},block_characteristic_contexts(host,SimpleNamespace(participants=(participant,))))
        for value in (True,0,2,'1'):
            with self.assertRaises(ValueError):block_characteristics_version(value)

    def test_runtime_queries_and_saved_block_facts_reject_unrepresented_fields(self):
        spec=qualified_block_pair_spec('Whenever this creature blocks a nonblack creature, destroy that creature at end of combat.')
        condition=qualifier(spec)
        for change in ({'source_executable':'callback'},{'value':False},{'op':'gte'},
                {'query':{**condition['query'],'owner':'A'}},{'query':{**condition['query'],'exclude_ref':'source'}}):
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                block_counterpart_matches({**condition,**change},pair_context(facts()))
        for bad in (facts(power=True),facts(types=['land']),facts(logical_object_id=''),facts(colors=['G','G']),{**facts(),'extra':1}):
            with self.assertRaises(ValueError):block_counterpart_matches(condition,pair_context(bad))
        with self.assertRaises(ValueError):block_counterpart_matches(condition,pair_context(facts(logical_object_id='forged@3')))
        program=SimpleNamespace(event_condition=spec.condition,event='creature.blocks',active_zone='battlefield')
        self.assertIn('trigger.event.block_counterpart_characteristics',qualified_block_runtime_dependencies(program))
        with self.assertRaises(ValueError):qualified_block_runtime_dependencies(SimpleNamespace(**{**vars(program),'event':'spell.cast'}))

    def test_checkpoint_and_manifest_marker_roundtrip_and_mismatch_remain_strict(self):
        from quorune.model import GameState
        from quorune.record_state_provenance import format_state_versions,validate_state_versions
        from quorune.model import GameConfig
        state=GameState(game_id='block-version',config=GameConfig(),players={},cards={},deck_names={},
            commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)
        raw=state.to_dict();self.assertEqual(1,raw['block_characteristics_version'])
        restored=GameState.from_dict(raw);self.assertEqual(1,restored.block_characteristics_version)
        old=deepcopy(raw);old.pop('block_characteristics_version');legacy=GameState.from_dict(old)
        self.assertIsNone(legacy.block_characteristics_version);self.assertNotIn('block_characteristics_version',legacy.to_dict())
        for value in (True,0,2,'1'):
            with self.assertRaises(ValueError):GameState.from_dict({**raw,'block_characteristics_version':value})
        manifest={'format':format_state_versions(state)}
        validate_state_versions(manifest,state)
        with self.assertRaises(ValueError):validate_state_versions({'format':{**manifest['format'],'block_characteristics_version':0}},state)

    def test_block_fact_color_omission_is_killed(self):
        import quorune.rules.block_characteristics as owner
        original=owner.object_matches_query
        def omit(row,query):
            from dataclasses import replace
            return original(row,replace(query,colors_none=()))
        with patch.object(owner,'object_matches_query',side_effect=omit):
            result=unittest.TestResult();QualifiedBlockPairOwnerTests('test_sealed_counterpart_queries_keep_color_type_subtype_numeric_and_exact_pair_identity').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)
