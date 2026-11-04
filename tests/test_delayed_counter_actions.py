"""CR122/603.7: fixed counter effects occur on the original object later."""
from types import SimpleNamespace
from unittest.mock import Mock,patch
from copy import deepcopy
import json,unittest
from common import ROOT
from quorune.delayed_object_model import SCHEDULE_OBJECT_ACTION,RESOLVE_OBJECT_ACTION,validate_delayed_object_instruction
from quorune.delayed_object_actions import bind_delayed_object_instruction,apply_delayed_object_action


class DelayedCounterOwnerTests(unittest.TestCase):
    def test_closed_counter_versions_names_amounts_and_retained_source(self):
        effect={'op':SCHEDULE_OBJECT_ACTION,'schema_version':2,'object':'$source.zone_object','action':'remove_counters',
            'counter':'+1/+1','amount':1,'source':'$source','timing':'end_of_combat'}
        validate_delayed_object_instruction(effect,compiled=True)
        item=SimpleNamespace(context={'source_logical_object_id':'old-object'})
        host=SimpleNamespace(_stack_source_ref=lambda item:'source-ref')
        bound=bind_delayed_object_instruction(host,effect,item)
        self.assertEqual({'reference':'source-ref','logical_object_id':'old-object'},bound['object'])
        self.assertEqual('source-ref',bound['source'])
        for change in ({'schema_version':1},{'action':'destroy'},{'amount':True},{'amount':0},{'amount':'X'},
                {'counter':'Oil'},{'counter':''},{'source':None},{'object':'$event.block_counterpart'}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_delayed_object_instruction({**effect,**change},compiled=True)
        with self.assertRaises(ValueError):validate_delayed_object_instruction({k:v for k,v in bound.items() if k!='counter'})
        legacy={'op':SCHEDULE_OBJECT_ACTION,'schema_version':1,'object':bound['object'],'action':'destroy','timing':'end_of_combat'}
        validate_delayed_object_instruction(legacy)
        with self.assertRaises(ValueError):validate_delayed_object_instruction({**legacy,'counter':'+1/+1'})

    def test_counter_dispatch_waits_and_retains_exact_kind_amount_and_original_incarnation(self):
        for action,counter,count in (('place_counters','-1/-1',2),('remove_counters','+1/+1',3)):
            with self.subTest(action=action):
                card=SimpleNamespace(ref='source-ref',logical_object_id='old-object',zone='battlefield',controller='B',phased_out=False)
                host=SimpleNamespace(state=SimpleNamespace(cards={'source-id':card},turn_sequence=6,delayed_triggers=[]),
                    _next_ref=Mock(return_value='DT1'),_stable_runtime_id=Mock(return_value='delayed:1'),_log=Mock(),apply_effect=Mock())
                instruction={'op':SCHEDULE_OBJECT_ACTION,'schema_version':2,'object':{'reference':'source-ref','logical_object_id':'old-object'},
                    'action':action,'counter':counter,'amount':count,'source':'source-ref','timing':'end_of_combat',
                    '_runtime_source':{'object_id':'source-id','logical_object_id':'old-object','card_ref':'source-ref','stack_ref':'S1'}}
                apply_delayed_object_action(host,instruction,actor='A',operation=SCHEDULE_OBJECT_ACTION,reason='Counter witness')
                self.assertFalse(host.apply_effect.called)
                future=host.state.delayed_triggers[0].stack_template['context']['dynamic_effects'][0]
                self.assertEqual(2,future['schema_version'])
                apply_delayed_object_action(host,future,actor='A',operation=RESOLVE_OBJECT_ACTION,reason='Counter witness')
                host.apply_effect.assert_called_once_with({'op':action,'card':'source-ref','reason':'Counter witness',
                    'counter':counter,'amount':count,'source':'source-ref'},actor='A')
                host.apply_effect.reset_mock();card.logical_object_id='returned-object'
                apply_delayed_object_action(host,future,actor='A',operation=RESOLVE_OBJECT_ACTION,reason='Counter witness')
                self.assertFalse(host.apply_effect.called)

    def test_fixed_counter_delays_compile_through_the_existing_counter_primitives(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for body,action,counter,count,capability in (
                ('remove a +1/+1 counter from it at end of combat.','remove_counters','+1/+1',1,'counter.removal.fixed_effect'),
                ('put two -1/-1 counters on it at end of combat.','place_counters','-1/-1',2,'counter.producer.fixed_effect'),
                ('put an oil counter on this creature at the beginning of the next end step.','place_counters','oil',1,'counter.producer.fixed_effect'),
                ('put a flying counter on this creature at end of combat.','place_counters','flying',1,'counter.characteristic.keyword')):
            with self.subTest(body=body):
                ir=compile_oracle_card(record('Whenever this creature attacks or blocks, '+body,'Artifact Creature — Construct'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals);node=ir.faces[0].nodes[0]
                self.assertIsNone(node.target_schema);self.assertIn(capability,node.capability_dependencies)
                effect=node.effects[0];self.assertEqual((2,action,counter,count),(effect['schema_version'],effect['action'],effect['counter'],effect['amount']))
        for body in ('remove all counters from it at end of combat.','remove X +1/+1 counters from it at end of combat.',
                'put a +1/+1 counter on target creature at end of combat.','put a +1/+1 counter on it at end of combat. Draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(record('Whenever this creature attacks or blocks, '+body,'Creature — Construct'),capability_registry=registry).status)

    def test_counter_primitive_withdrawal_blocks_delays_without_removing_movement_delays(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        original=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json');known=set(discover_tests(ROOT).values())
        for capability,body in (('counter.removal.fixed_effect','remove a +1/+1 counter from it at end of combat.'),
                ('counter.producer.fixed_effect','put a -1/-1 counter on it at end of combat.'),
                ('counter.characteristic.keyword','put a flying counter on this creature at end of combat.')):
            raw=deepcopy(original);next(c for c in raw['capabilities'] if c['id']==capability).update(status='blocked',blockers=['primitive withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(record('Whenever this creature attacks or blocks, '+body,'Creature — Construct'),capability_registry=registry).status)
            self.assertEqual('exact',compile_oracle_card(record('Whenever this creature attacks or blocks, sacrifice it at end of combat.','Creature — Construct'),capability_registry=registry).status)

    def test_delayed_counter_amount_omission_is_killed(self):
        import quorune.delayed_object_actions as owner
        original=owner.apply_delayed_object_action
        def omit(host,effect,**kwargs):
            if effect['op']==RESOLVE_OBJECT_ACTION and effect['schema_version']==2:effect={**effect,'amount':1}
            return original(host,effect,**kwargs)
        with patch(__name__+'.apply_delayed_object_action',side_effect=omit):
            result=unittest.TestResult();DelayedCounterOwnerTests('test_counter_dispatch_waits_and_retains_exact_kind_amount_and_original_incarnation').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)
