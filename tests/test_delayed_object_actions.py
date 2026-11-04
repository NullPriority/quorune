"""CR400.7/603.7: future actions retain one exact object, controller and step."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import json
from common import ROOT
from quorune.delayed_object_actions import apply_delayed_object_action,bind_delayed_object_instruction
from quorune.delayed_object_model import (SCHEDULE_OBJECT_ACTION,RESOLVE_OBJECT_ACTION,
    validate_delayed_object_instruction,delayed_object_node_capabilities,DELAYED_OBJECT_MECHANIC)
from quorune.delayed_triggers import materialize_delayed_trigger
from quorune.model import DelayedTrigger
from quorune.trigger_processing import TriggerProcessingOwner


def instruction(action='destroy',timing='end_of_combat'):
    return {'op':SCHEDULE_OBJECT_ACTION,'schema_version':1,'object':{'reference':'body','logical_object_id':'body@0'},
        'action':action,'timing':timing}


class DelayedObjectOwnerTests(unittest.TestCase):
    def test_next_end_step_is_not_creation_time_or_the_controllers_own_end_step_only(self):
        host=SimpleNamespace(state=SimpleNamespace(cards={},turn_sequence=4,delayed_triggers=[]),
            _next_ref=Mock(return_value='DT1'),_stable_runtime_id=Mock(return_value='delayed:1'),_log=Mock(),apply_effect=Mock())
        effect={**instruction('bounce','next_end_step'),'_runtime_source':{
            'object_id':'source-id','logical_object_id':'source@0','card_ref':'source','stack_ref':'S1'}}
        apply_delayed_object_action(host,effect,actor='A',operation=SCHEDULE_OBJECT_ACTION,reason='Next end step')
        trigger=host.state.delayed_triggers[0];self.assertTrue(trigger.active)
        self.assertFalse(host.apply_effect.called)
        owner=TriggerProcessingOwner(host)
        for phase,step in (('combat','end_combat'),('ending','cleanup'),('beginning','upkeep')):
            self.assertFalse(owner.trigger_matches(trigger,'step.begin',{'phase':phase,'step':step,'player':'B'}))
        self.assertTrue(owner.trigger_matches(trigger,'step.begin',{'phase':'ending','step':'end_step','player':'B'}))
        self.assertEqual('A',trigger.controller)

    def test_delayed_source_and_block_counterpart_compile_without_targets_and_reject_open_bodies(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for text,action,reference,timing in (
                ('When this creature blocks, destroy it at end of combat.','destroy','$source.zone_object','end_of_combat'),
                ('When this creature attacks or blocks, sacrifice it at end of combat.','sacrifice','$source.zone_object','end_of_combat'),
                ("When this creature blocks, return it to its owner's hand at end of combat.",'bounce','$source.zone_object','end_of_combat'),
                ('Whenever this creature blocks or becomes blocked by a creature, destroy that creature at end of combat.','destroy','$event.block_counterpart','end_of_combat'),
                ('When this creature attacks or blocks, sacrifice it at the beginning of the next end step.','sacrifice','$source.zone_object','next_end_step')):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature — Elemental'),capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertIsNone(node.target_schema)
                self.assertEqual((action,reference,timing),(node.effects[0]['action'],node.effects[0]['object'],node.effects[0]['timing']))
        for body in ('destroy target creature at end of combat.','destroy it at end of combat. Draw a card.',
                'destroy it at the beginning of the next cleanup step.','destroy all creatures at end of combat.'):
            ir=compile_oracle_card(record('When this creature blocks, '+body,'Creature — Elemental'),capability_registry=registry)
            self.assertNotEqual('exact',ir.status)
        qualified=compile_oracle_card(record('Whenever this creature blocks or becomes blocked by a nonblack creature, destroy that creature at end of combat.','Creature — Elemental'),capability_registry=registry)
        self.assertEqual('exact',qualified.status,qualified.material_residuals)
        linked=compile_oracle_card(record('Whenever this creature blocks or becomes blocked by a creature that shares a color with it, destroy that creature at end of combat.','Creature — Elemental'),capability_registry=registry)
        self.assertNotEqual('exact',linked.status,'Context-relative color sharing remains unrepresented')

    def test_verified_delayed_object_withdrawal_preserves_immediate_destruction(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from test_granted_skulk import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id']=='trigger.delayed.fixed_object_action').update(status='blocked',blockers=['retained delayed authority withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(record('When this creature blocks, destroy it at end of combat.','Creature — Wall'),capability_registry=registry)
        self.assertNotEqual('exact',ir.status)
        immediate=compile_oracle_card(record('Destroy target creature.','Sorcery'),capability_registry=registry)
        self.assertEqual('exact',immediate.status,immediate.material_residuals)

    def test_closed_action_timing_and_compiled_reference_schema(self):
        for action in ('destroy','sacrifice','bounce'):
            for timing in ('end_of_combat','next_end_step'):
                effect=instruction(action,timing);validate_delayed_object_instruction(effect)
                compiled={**effect,'object':'$source.zone_object'}
                self.assertTrue(delayed_object_node_capabilities(effects=(compiled,),target_schema=None,mechanic_ids=(DELAYED_OBJECT_MECHANIC,)))
        for change in ({'schema_version':True},{'schema_version':2},{'action':'execute_arbitrary'},
                {'timing':'cleanup'},{'extra':1},{'object':{'reference':'body'}},
                {'object':{'reference':'body','logical_object_id':''}}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_delayed_object_instruction({**instruction(),**change})
        compiled={**instruction(),'object':'$target.0'}
        self.assertFalse(delayed_object_node_capabilities(effects=(compiled,),target_schema=None,mechanic_ids=(DELAYED_OBJECT_MECHANIC,)))

    def test_delayed_action_keeps_identity_source_controller_and_waits_for_the_right_step(self):
        card=SimpleNamespace(ref='body',logical_object_id='body@0',zone='battlefield',controller='A',phased_out=False)
        host=SimpleNamespace(state=SimpleNamespace(cards={'body-id':card},turn_sequence=9,delayed_triggers=[]),
            _next_ref=Mock(return_value='DT1'),_stable_runtime_id=Mock(return_value='delayed:1'),_log=Mock(),apply_effect=Mock())
        effect={**instruction(),'_runtime_source':{'object_id':'departed-source','logical_object_id':'source@0','card_ref':'source','stack_ref':'S1'}}
        self.assertEqual('DT1',apply_delayed_object_action(host,effect,actor='A',operation=SCHEDULE_OBJECT_ACTION,reason='Rule witness'))
        self.assertFalse(host.apply_effect.called)
        trigger=DelayedTrigger.from_dict(host.state.delayed_triggers[0].to_dict())
        self.assertEqual(('A','departed-source','source@0',True),(trigger.controller,trigger.source_object_id,trigger.source_logical_object_id,trigger.once))
        owner=TriggerProcessingOwner(host)
        self.assertFalse(owner.trigger_matches(trigger,'step.begin',{'phase':'combat','step':'combat_damage'}))
        self.assertFalse(owner.trigger_matches(trigger,'step.begin',{'phase':'ending','step':'end_step'}))
        self.assertTrue(owner.trigger_matches(trigger,'step.begin',{'phase':'combat','step':'end_combat'}))
        item=materialize_delayed_trigger(trigger,ref='S2',stack_id='stack:2',visibility=('A','B','C','D'))
        self.assertEqual(('A','departed-source','source@0'),(item.controller,item.source_object_id,item.context['source_logical_object_id']))
        future=item.context['dynamic_effects'][0]
        card.controller='B'
        apply_delayed_object_action(host,future,actor='A',operation=RESOLVE_OBJECT_ACTION,reason='Delayed witness')
        host.apply_effect.assert_called_once_with({'op':'destroy','card':'body','reason':'Delayed witness'},actor='A')
        host.apply_effect.reset_mock();card.logical_object_id='body@1'
        apply_delayed_object_action(host,future,actor='A',operation=RESOLVE_OBJECT_ACTION,reason='Delayed witness')
        self.assertFalse(host.apply_effect.called)
        card.logical_object_id='body@0';card.phased_out=True
        apply_delayed_object_action(host,future,actor='A',operation=RESOLVE_OBJECT_ACTION,reason='Delayed witness')
        self.assertFalse(host.apply_effect.called)
        card.phased_out=False;future={**future,'action':'sacrifice'}
        apply_delayed_object_action(host,future,actor='A',operation=RESOLVE_OBJECT_ACTION,reason='Delayed witness')
        self.assertFalse(host.apply_effect.called)
        card.controller='A'
        apply_delayed_object_action(host,future,actor='A',operation=RESOLVE_OBJECT_ACTION,reason='Delayed witness')
        self.assertEqual('sacrifice',host.apply_effect.call_args.args[0]['op'])

    def test_capture_keeps_old_incarnation_even_when_the_object_has_already_departed(self):
        host=SimpleNamespace(_stack_source_ref=lambda item:'source-ref')
        item=SimpleNamespace(context={'source_logical_object_id':'source@0','block_counterpart_recipient':{'reference':'other','logical_object_id':'other@0'}})
        for reference,expected in (('$source.zone_object',{'reference':'source-ref','logical_object_id':'source@0'}),
                ('$event.block_counterpart',{'reference':'other','logical_object_id':'other@0'})):
            effect={**instruction(),'object':reference}
            self.assertEqual(expected,bind_delayed_object_instruction(host,effect,item)['object'])
        item.context.pop('source_logical_object_id')
        with self.assertRaises(ValueError):bind_delayed_object_instruction(host,{**instruction(),'object':'$source.zone_object'},item)

    def test_omitting_the_retained_incarnation_check_is_killed(self):
        import quorune.delayed_object_actions as owner
        original=owner.apply_delayed_object_action
        def omit(host,effect,**kwargs):
            if effect['op']==RESOLVE_OBJECT_ACTION:
                card=next(iter(host.state.cards.values()))
                effect={**effect,'object':{**effect['object'],'logical_object_id':card.logical_object_id}}
            return original(host,effect,**kwargs)
        with patch(__name__+'.apply_delayed_object_action',side_effect=omit):
            result=unittest.TestResult();DelayedObjectOwnerTests('test_delayed_action_keeps_identity_source_controller_and_waits_for_the_right_step').run(result)
        self.assertFalse(result.errors,result.errors);self.assertTrue(result.failures)
