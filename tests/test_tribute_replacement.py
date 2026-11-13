"""A Tribute replacement finishes its opponent decision before proceeding."""
from dataclasses import replace
import unittest

from quorune.tribute_model import TributeSpec,TRIBUTE_ENTRY_EVENT
from quorune.replacement.model import AffectedObject,ReplaceableEvent,ReplacementEventBatch,ReplacementEffect,ReplacementClass
from quorune.replacement.operations import SetField,AddAmount,operation_from_dict,operation_to_dict
from quorune.replacement.ordering import advance_replacement_batch,resolve_replacement_batch
from quorune.replacement.tribute import (tribute_entry_replacements,OpenTributeEntryChoice,
    CompleteTributeEntryChoice,validate_tribute_choice_event)


class TributeReplacementTests(unittest.TestCase):
    def event(self,ref='entrant',entry_id='entry:1'):
        return ReplaceableEvent(event_id=entry_id,kind='zone.change',affected_player=None,
            affected_object=AffectedObject('object:'+ref,'D','A'),
            payload={'object_ref':ref,'object_types':['creature'],'logical_object_id':'object:'+ref+'@0',
                'destination':'battlefield','destination_controller':'A'})

    def effects(self,ref='entrant'):
        return tribute_entry_replacements(TributeSpec(3,'front:tribute:1'),object_ref=ref,
            component_id='component:1',players=('A','B','C','D'))

    def test_controller_selects_one_opponent_and_only_that_player_decides_counters(self):
        event=self.event();effects=self.effects();batch=ReplacementEventBatch(batch_id='batch',events=(event,),apnap_order=('A','B','C','D'))
        start=advance_replacement_batch(batch,effects)
        self.assertEqual('A',start.pending.choice.chooser)
        self.assertEqual(3,len(start.pending.choice.options))
        root=next(v for v in start.pending.choice.options if v.endswith('opponent:C'))
        opened=advance_replacement_batch(batch,effects,selections=[root])
        self.assertEqual('C',opened.pending.choice.chooser)
        self.assertEqual(TRIBUTE_ENTRY_EVENT,opened.pending.choice.event.kind)
        self.assertEqual((),event.children)
        self.assertEqual((),opened.pending.choice.event.children)
        decision=opened.pending.choice.options[0]
        for accepted in (False,True):
            with self.subTest(accepted=accepted):
                result=advance_replacement_batch(batch,effects,selections=[root,decision if accepted else 'decline:'+decision])
                self.assertIsNone(result.pending)
                child,=result.batch.events[0].children
                self.assertEqual(accepted,child.payload['accepted'])
                if accepted:
                    counter,=child.children;self.assertEqual('counter.place',counter.kind)
                    self.assertEqual(('C','A',3),(counter.payload['placing_player'],counter.payload['target_controller'],counter.payload['amount']))
                    self.assertEqual('D',counter.affected_object.owner)
                else:self.assertEqual((),child.children)
                self.assertEqual(result.batch,resolve_replacement_batch(batch,effects,selections=result.batch.journal))

    def test_applied_replacement_finishes_opponent_choice_before_other_root_and_batch_choices(self):
        first,second=self.event(),self.event('second','entry:2')
        effects=(*self.effects(),*self.effects('second'),ReplacementEffect(effect_id='z:tap',source_id='external',
            event_kind='zone.change',replacement_class=ReplacementClass.OTHER,
            conditions={'object_ref':{'eq':'entrant'}},operations=(SetField('tapped',True),),optional=True))
        batch=ReplacementEventBatch(batch_id='batch',events=(first,second),apnap_order=('A','B','C','D'))
        root=next(e.effect_id for e in effects if e.effect_id.startswith('tribute:entrant:') and e.effect_id.endswith('opponent:B'))
        progress=advance_replacement_batch(batch,effects,selections=[root])
        self.assertEqual('B',progress.pending.choice.chooser)
        self.assertEqual('entry:1',progress.pending.event_id)
        self.assertEqual(TRIBUTE_ENTRY_EVENT,progress.pending.choice.event.kind)
        self.assertFalse(progress.batch.events[0].payload.get('tapped',False))
        self.assertEqual((),progress.batch.events[1].children)

    def test_counter_quantity_replacements_distinguish_the_opponent_from_the_controller(self):
        # The represented placing-player predicate is exercised independently
        # of any whole-card support claim for a quantity-replacement source.
        for placing_player,amount in (('A',3),('C',4)):
            with self.subTest(placing_player=placing_player):
                quantity=ReplacementEffect(effect_id='quantity',source_id='counter-source',
                    event_kind='counter.place',replacement_class=ReplacementClass.OTHER,
                    conditions={'placing_player':{'eq':placing_player}},operations=(AddAmount('amount',1),))
                effects=(*self.effects(),quantity)
                batch=ReplacementEventBatch(batch_id='batch',events=(self.event(),),apnap_order=('A','B','C','D'))
                root=next(e.effect_id for e in effects if e.effect_id.endswith('opponent:C'))
                choices=[root,root+':decision']
                result=advance_replacement_batch(batch,effects,selections=choices)
                self.assertIsNone(result.pending)
                tribute,=result.batch.events[0].children
                counter,=tribute.children
                self.assertTrue(tribute.payload['accepted'])
                self.assertEqual(('C','A',amount),(counter.payload['placing_player'],
                    counter.payload['target_controller'],counter.payload['amount']))
                self.assertEqual(result.batch,resolve_replacement_batch(batch,effects,selections=result.batch.journal))

    def test_prohibition_automatically_declines_and_cannot_be_overridden_by_a_saved_acceptance(self):
        from quorune.counter_permission import CounterPermissionSnapshot,CounterProhibitionRule,FixedCounterProhibitionSpec
        permissions=CounterPermissionSnapshot((CounterProhibitionRule(
            FixedCounterProhibitionSpec('permanent','any',types_any=('creature',)),'prohibitor','B'),))
        event=self.event();event=replace(event,payload={**event.payload,'_counter_permissions':permissions.to_dict()})
        batch=ReplacementEventBatch(batch_id='batch',events=(event,),apnap_order=('A','B','C','D'))
        effects=self.effects();root=next(e.effect_id for e in effects if e.effect_id.endswith('opponent:C'))
        result=advance_replacement_batch(batch,effects,selections=[root])
        self.assertIsNone(result.pending)
        tribute,=result.batch.events[0].children
        self.assertFalse(tribute.payload['accepted']);self.assertEqual((),tribute.children)
        self.assertEqual(result.batch,resolve_replacement_batch(batch,effects,selections=result.batch.journal))
        with self.assertRaises(ValueError):advance_replacement_batch(batch,effects,selections=[root,root+':decision'])

    def test_choice_operations_roundtrip_and_saved_child_binding_rejects_cross_entry_or_self(self):
        spec=TributeSpec(2,'link');ops=(OpenTributeEntryChoice(spec,'B',('A','B'),'choice'),CompleteTributeEntryChoice(spec,True))
        for op in ops:self.assertEqual(op,operation_from_dict(operation_to_dict(op)))
        batch=ReplacementEventBatch(batch_id='batch',events=(self.event(),),apnap_order=('A','B','C','D'));effects=self.effects()
        root=next(e.effect_id for e in effects if e.effect_id.endswith('opponent:B'))
        child=advance_replacement_batch(batch,effects,selections=[root]).pending.choice.event
        validate_tribute_choice_event(child,pending=True)
        for change in ({'opponent':'A'},{'replacement_id':'another'},{'accepted':True},{'unknown':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_tribute_choice_event(replace(child,payload={**child.payload,**change}),pending=True)

    def test_nested_tribute_counters_follow_the_final_zone_destination(self):
        from quorune.replacement.ordering import replacement_tree_choice,apply_tree_replacement
        event=self.event();effects=self.effects()
        root=next(e.effect_id for e in effects if e.effect_id.endswith('opponent:C'))
        batch=ReplacementEventBatch(batch_id='batch',events=(event,),apnap_order=('A','B','C','D'))
        entered=advance_replacement_batch(batch,effects,selections=[root,root+':decision']).batch.events[0]
        redirect=ReplacementEffect(effect_id='redirect',source_id='destination-source',event_kind='zone.change',
            replacement_class=ReplacementClass.OTHER,operations=(SetField('destination','exile'),))
        pending=replacement_tree_choice(entered,(redirect,))
        redirected=apply_tree_replacement(entered,(redirect,),pending,'redirect')
        counter=redirected.children[0].children[0]
        self.assertEqual(('exile','card',None),(counter.payload['target_zone'],
            counter.payload['target_kind'],counter.payload['target_controller']))
        self.assertIsNone(counter.affected_object.controller)
        self.assertEqual('C',counter.payload['placing_player'])

    def test_copied_tribute_rebinds_the_shared_occurrence_and_only_its_selected_form(self):
        from quorune.replacement.tribute import copied_tribute_replacement
        prefix='copied-entry:original:'
        effects=tuple(copied_tribute_replacement(e,prefix=prefix,form_key='selected') for e in self.effects())
        event=self.event();event=replace(event,payload={**event.payload,'entry_copy_form':'other'})
        batch=ReplacementEventBatch(batch_id='batch',events=(event,),apnap_order=('A','B','C','D'))
        self.assertEqual((),advance_replacement_batch(batch,effects).batch.events[0].children)
        event=replace(event,payload={**event.payload,'entry_copy_form':'selected'})
        batch=replace(batch,events=(event,))
        root=next(e.effect_id for e in effects if e.effect_id.endswith('opponent:C'))
        result=advance_replacement_batch(batch,effects,selections=[root,root+':decision'])
        self.assertIsNone(result.pending)
        tribute,=result.batch.events[0].children
        self.assertEqual(root,tribute.payload['replacement_id'])
        self.assertEqual(('C',3),(tribute.children[0].payload['placing_player'],tribute.children[0].payload['amount']))
        self.assertEqual(result.batch,resolve_replacement_batch(batch,effects,selections=result.batch.journal))
        root_effect=next(e for e in effects if e.effect_id==root)
        for guard in (True,{'eq':True},{'eq':''},{'eq':'selected','extra':1}):
            with self.subTest(guard=guard),self.assertRaises(ValueError):
                replace(root_effect,conditions={**root_effect.conditions,'entry_copy_form':guard})

    def test_prospective_nested_counter_keeps_the_containing_zone_identity_and_rejects_tampering(self):
        from types import SimpleNamespace
        from quorune.counter_placement import validate_counter_event_subjects
        event=self.event();event=replace(event,payload={**event.payload,'origin':'outside',
            'prospective_subject':True,'logical_object_id':'object:entrant@1'})
        effects=self.effects();root=next(e.effect_id for e in effects if e.effect_id.endswith('opponent:C'))
        batch=ReplacementEventBatch(batch_id='batch',events=(event,),apnap_order=('A','B','C','D'))
        entered=advance_replacement_batch(batch,effects,selections=[root,root+':decision']).batch.events[0]
        host=SimpleNamespace(state=SimpleNamespace(cards={}))
        validate_counter_event_subjects(host,(entered,))
        tribute,=entered.children;counter,=tribute.children
        for change in ({'target_logical_object_id':'another@1'},{'target_zone':'exile'},{'prospective_subject':False}):
            changed=replace(counter,payload={**counter.payload,**change})
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_counter_event_subjects(host,(replace(entered,children=(replace(tribute,children=(changed,)),)),))

    def test_captured_permissions_refresh_nested_counters_after_entry_retargeting(self):
        from quorune.counter_permission import CounterPermissionSnapshot,CounterProhibitionRule,FixedCounterProhibitionSpec
        from quorune.counter_permission_events import refresh_entry_counter_permissions,counter_event_is_prohibited
        from quorune.replacement.immutable import thaw_value
        event=self.event();effects=self.effects()
        root=next(e.effect_id for e in effects if e.effect_id.endswith('opponent:C'))
        batch=ReplacementEventBatch(batch_id='batch',events=(event,),apnap_order=('A','B','C','D'))
        entered=advance_replacement_batch(batch,effects,selections=[root,root+':decision']).batch.events[0]
        permissions=CounterPermissionSnapshot((CounterProhibitionRule(
            FixedCounterProhibitionSpec('permanent','any',types_any=('creature',)),'prohibitor','B'),))
        payload={**thaw_value(entered.payload),'_counter_permissions':permissions.to_dict()}
        children=list(entered.children)
        refresh_entry_counter_permissions(entered,payload,children)
        denied=children[0].children[0]
        self.assertTrue(counter_event_is_prohibited(denied));self.assertEqual(0,denied.payload['amount'])
        refresh_entry_counter_permissions(entered,{**payload,'destination':'exile','destination_controller':None},children)
        allowed=children[0].children[0]
        self.assertFalse(counter_event_is_prohibited(allowed));self.assertEqual(3,allowed.payload['amount'])
        self.assertEqual(('exile','card',None),(allowed.payload['target_zone'],allowed.payload['target_kind'],allowed.payload['target_controller']))
        self.assertEqual(3,entered.children[0].children[0].payload['amount'])

    def test_shared_occurrences_and_complementary_decisions_cannot_be_forged(self):
        effects=self.effects();root=next(e for e in effects if e.effect_id.endswith('opponent:B'))
        child=next(e for e in effects if e.effect_id==root.effect_id+':decision')
        for change in ({'occurrence_id':None},{'optional':True},{'event_kind':'damage'},
                {'effect_id':'other'},{'conditions':{'destination':{'eq':'battlefield'}}}):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(root,**change)
        for change in ({'decline_operations':()},{'optional':False},
                {'conditions':{**child.conditions,'replacement_id':True}},
                {'operations':(SetField('tapped',True),)}):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(child,**change)
