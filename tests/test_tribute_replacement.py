"""A Tribute replacement finishes its opponent decision before proceeding."""
from dataclasses import replace
import unittest

from quorune.tribute_model import TributeSpec,TRIBUTE_ENTRY_EVENT
from quorune.replacement.model import AffectedObject,ReplaceableEvent,ReplacementEventBatch,ReplacementEffect,ReplacementClass
from quorune.replacement.operations import SetField,operation_from_dict,operation_to_dict
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
