"""CR603.8: pending instances, not priority decisions, suppress a state trigger."""
from dataclasses import replace
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.state_trigger_model import PublicStateTriggerSpec,STATE_TRIGGER_EVENT,STATE_TRIGGER_FIELD,STATE_TRIGGER_IDENTITY
from quorune.rules.state_triggers import state_trigger_available,state_trigger_runtime_dependencies
from quorune.semantics import SemanticProgram,SemanticRegistry
from quorune.model import StackItem
from quorune.trigger_batches import PendingTriggerItem,create_pending_trigger_batch
from quorune.source_maintenance import SourceMaintenanceSpec

def spec():return PublicStateTriggerSpec(fixed_public_state_condition('you control no artifacts',source_name='Generic State Source'))

def program(key='state-model',event=STATE_TRIGGER_EVENT):
    s=spec()
    return SemanticProgram(key=key,label='Public condition',ability_id='trigger:front:n1',active_zone='battlefield',event=event,
        event_condition=s.event_condition(),effects=[SourceMaintenanceSpec().effect()])

class StateTriggerModelTests(unittest.TestCase):
    def test_state_subscription_codec_and_runtime_shape_are_closed(self):
        s=spec();self.assertEqual(s,PublicStateTriggerSpec.from_dict(s.to_dict()));self.assertEqual(s,PublicStateTriggerSpec.from_condition(s.event_condition()))
        for change in ({'schema_version':True},{'schema_version':2},{'extra':0}):
            with self.assertRaises(ValueError):PublicStateTriggerSpec.from_dict({**s.to_dict(),**change})
        for change in ({'op':'eq'},{'value':1},{'extra':True}):
            with self.assertRaises(ValueError):PublicStateTriggerSpec.from_condition({**s.event_condition(),**change})
        p=program();self.assertIn('trigger.state.fixed_public_condition',state_trigger_runtime_dependencies(p))
        for p in (replace(p,event='step.begin'),replace(p,active_zone='hand'),replace(p,target_schema={'count':1}),replace(p,effects=[])):
            with self.assertRaises(ValueError):state_trigger_runtime_dependencies(p)

    def test_pending_stack_and_immutable_batches_suppress_only_the_same_incarnation(self):
        source=SimpleNamespace(zone='battlefield',phased_out=False,object_id='source',logical_object_id='source@0')
        p=program();state=SimpleNamespace(stack=[],pending_trigger_batches=[]);host=SimpleNamespace(state=state,_fixed_public_state_condition_holds=lambda *_:True)
        self.assertTrue(state_trigger_available(host,source,p))
        item=StackItem('id:S1','S1','triggered_ability','A','State',source_object_id='source',semantic_key=p.key,context={STATE_TRIGGER_IDENTITY:'source@0'})
        state.stack.append(item);self.assertFalse(state_trigger_available(host,source,p));state.stack.clear();self.assertTrue(state_trigger_available(host,source,p))
        batch=create_pending_trigger_batch(batch_id='batch',ref='TB1',items=[PendingTriggerItem.from_dict(item.to_dict())],apnap_order=['A','B'],turn_sequence=1,priority_epoch=1);state.pending_trigger_batches=[batch]
        self.assertFalse(state_trigger_available(host,source,p));source.logical_object_id='source@1';self.assertTrue(state_trigger_available(host,source,p));source.phased_out=True;self.assertFalse(state_trigger_available(host,source,p))

    def test_pending_latch_omission_mutant_is_killed(self):
        import quorune.rules.state_triggers as owner
        source=SimpleNamespace(zone='battlefield',phased_out=False,object_id='source',logical_object_id='source@0');p=program()
        item=StackItem('id:S1','S1','triggered_ability','A','State',source_object_id='source',semantic_key=p.key,context={STATE_TRIGGER_IDENTITY:'source@0'})
        host=SimpleNamespace(state=SimpleNamespace(stack=[item],pending_trigger_batches=[]),_fixed_public_state_condition_holds=lambda *_:True)
        def witness():self.assertFalse(owner.state_trigger_available(host,source,p))
        witness()
        with patch.object(owner,'_same_occurrence',return_value=False),self.assertRaises(AssertionError):witness()

    def test_registry_event_index_tracks_replace_remove_and_exact_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'registry.json';registry=SemanticRegistry(path,include_builtin_packs=False);self.assertEqual((),registry.programs_for_event(STATE_TRIGGER_EVENT))
            p=program();registry.put(p);self.assertEqual((p,),registry.programs_for_event(STATE_TRIGGER_EVENT));registry.put(replace(p,event='step.begin',event_condition=None));self.assertEqual((),registry.programs_for_event(STATE_TRIGGER_EVENT))
            registry.put(p);loaded=SemanticRegistry(path,include_builtin_packs=False);self.assertEqual([p.to_dict()],[v.to_dict() for v in loaded.programs_for_event(STATE_TRIGGER_EVENT)]);loaded.remove(p.key);registry.load();self.assertEqual((),registry.programs_for_event(STATE_TRIGGER_EVENT))

    def test_source_relative_absence_and_snow_use_existing_public_quantity_shapes(self):
        from quorune.compiler.state_trigger_nodes import _state_condition
        c=_state_condition('you control no other creatures','Generic State Source');self.assertTrue(c.quantity.exclude_source)
        c=_state_condition('you control no snow lands','Generic State Source');self.assertEqual(('snow',),c.quantity.query.supertypes_all)
        self.assertIsNone(_state_condition('you control no permanents of the chosen color','Generic State Source'))
