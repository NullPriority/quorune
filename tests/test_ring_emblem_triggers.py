from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from quorune.model import StackItem
from quorune.ring_model import RingBearerSnapshot, RingEmblemState, RingObjectIdentity, RingRuleView, RingStateError
from quorune.ring_triggers import (
    RingCombatOccurrence, RingTriggerKind,
    materialize_ring_trigger, ring_trigger_specs,
)
from quorune.source_continuity import SourceContinuitySnapshot
from quorune.target_source import stack_targeting_source
from quorune.trigger_processing import schedule_delayed_trigger
from quorune.delayed_triggers import materialize_delayed_trigger


class RingTriggerFixture:
    seats = ['A','B','C']
    def __init__(self):self.sequence=0
    def _next_ref(self,prefix):
        self.sequence+=1
        return prefix+str(self.sequence)
    def _stable_runtime_id(self,kind,ref):return kind+':'+ref


class RingEmblemTriggerValueTests(unittest.TestCase):
    def setUp(self):
        self.emblem = RingObjectIdentity('emblem:A','E1','emblem:A:0')
        self.bearer = RingObjectIdentity('creature:A','C1','creature:A:0')
        self.blocker = RingObjectIdentity('creature:B','C2','creature:B:0')
        self.snapshot = RingBearerSnapshot(self.bearer,SourceContinuitySnapshot(0,0,0))

    def view(self,count):return RingRuleView(RingEmblemState('A',self.emblem,count,self.snapshot),7)

    def occurrence(self,kind,**kwargs):
        return RingCombatOccurrence(kind,self.bearer,'A',**kwargs)

    def test_only_gained_abilities_trigger_and_actual_combat_damage_may_hit_any_player(self):
        events = (
            (2,self.occurrence(RingTriggerKind.ATTACK)),
            (3,self.occurrence(RingTriggerKind.BLOCKED_BY_CREATURE,blocker=self.blocker)),
            (4,self.occurrence(RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER,
                               damaged_player='A',dealt_amount=1,combat=True)),
        )
        for count in range(1,7):
            for required,event in events:
                with self.subTest(count=count,kind=event.kind):
                    self.assertEqual(int(count>=required),len(ring_trigger_specs((self.view(count),),event)))
        for combat,amount in ((False,2),(True,0)):
            event=self.occurrence(RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER,
                                  damaged_player='B',dealt_amount=amount,combat=combat)
            self.assertEqual((),ring_trigger_specs((self.view(4),),event))

    def test_each_new_blocker_relationship_gets_its_own_sealed_blocker_and_emblem_source(self):
        other = RingObjectIdentity('creature:C','C3','creature:C:0')
        host = RingTriggerFixture()
        items=[]
        for blocker in (self.blocker,other):
            event=self.occurrence(RingTriggerKind.BLOCKED_BY_CREATURE,blocker=blocker)
            spec=ring_trigger_specs((self.view(3),),event)[0]
            item=materialize_ring_trigger(host,spec)
            self.assertEqual(self.emblem.object_id,item.source_object_id)
            self.assertEqual([blocker.object_id],item.referred_object_ids)
            delay=item.context['dynamic_effects'][0]
            self.assertEqual('delayed_trigger',delay['op'])
            self.assertEqual(blocker.to_dict(),delay['stack']['context']['dynamic_effects'][0]['object_identity'])
            items.append(item)
        self.assertNotEqual(items[0].stack_id,items[1].stack_id)

    def test_attack_and_life_effects_keep_late_controller_bindings_when_copied(self):
        host = RingTriggerFixture()
        spec=ring_trigger_specs((self.view(2),),self.occurrence(RingTriggerKind.ATTACK))[0]
        item=materialize_ring_trigger(host,spec)
        self.assertEqual('$controller',item.context['dynamic_effects'][0]['player'])
        self.assertEqual(['$controller'],item.context['dynamic_effects'][1]['players'])
        self.assertTrue(item.context['dynamic_effects'][1]['hidden'])
        copied=StackItem.from_dict(json.loads(json.dumps(item.to_dict())))
        copied.controller='B'
        source=stack_targeting_source(SimpleNamespace(state=SimpleNamespace(cards={})),copied)
        self.assertEqual('A',source.source_controller)
        self.assertEqual(frozenset(),source.card_types)
        self.assertEqual(frozenset(),source.colors)
        self.assertEqual('B',copied.controller)
        damage=self.occurrence(RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER,
                               damaged_player='B',dealt_amount=2,combat=True)
        life=ring_trigger_specs((self.view(4),),damage)[0].effects()
        self.assertEqual(({'op':'lose_life_each_opponent','amount':3},),life)

    def test_occurrence_identity_controller_and_designation_are_separate_requirements(self):
        event=self.occurrence(RingTriggerKind.ATTACK)
        self.assertEqual((),ring_trigger_specs((self.view(4),),replace(event,bearer_controller='B')))
        self.assertEqual((),ring_trigger_specs((self.view(4),),replace(event,
            bearer=replace(self.bearer,logical_object_id='creature:A:1'))))
        view=replace(self.view(4),state=self.view(4).state.without_bearer())
        self.assertEqual((),ring_trigger_specs((view,),event))

    def test_malformed_or_ambiguous_occurrence_facts_reject(self):
        for kwargs in ({'kind':'attack'},{'blocker':self.blocker},{'combat':True}):
            values={'kind':RingTriggerKind.ATTACK,'bearer':self.bearer,'bearer_controller':'A',**kwargs}
            with self.subTest(values=values),self.assertRaises(RingStateError):
                RingCombatOccurrence(**values)
        with self.assertRaises(RingStateError):
            self.occurrence(RingTriggerKind.BLOCKED_BY_CREATURE)
        with self.assertRaises(RingStateError):
            self.occurrence(RingTriggerKind.COMBAT_DAMAGE_TO_PLAYER,
                            damaged_player='B',dealt_amount=True,combat=True)

    def test_delayed_source_uses_captured_incarnation_after_source_reentry(self):
        fixture=RingTriggerFixture()
        host=SimpleNamespace(state=SimpleNamespace(
            cards={'source':SimpleNamespace(logical_object_id='source:1')},
            turn_sequence=3,delayed_triggers=[]),
            _next_ref=fixture._next_ref,_stable_runtime_id=fixture._stable_runtime_id,
            _log=lambda *args,**kwargs:None)
        delayed=schedule_delayed_trigger(host,controller='B',label='Captured source',
            event_kind='step.begin',condition={'step':'end_combat'},stack_template={},
            source_object_id='source',source_logical_object_id='source:0')
        item=materialize_delayed_trigger(delayed,ref='S9',stack_id='stack:S9',visibility=('A','B'))
        self.assertEqual('source:0',item.context['source_logical_object_id'])
        self.assertEqual('source',item.source_object_id)
        before=len(host.state.delayed_triggers)
        with self.assertRaises(ValueError):
            schedule_delayed_trigger(host,controller='A',label='Malformed captured source',
                event_kind='step.begin',condition={},stack_template={},
                source_object_id=None,source_logical_object_id='source:0')
        self.assertEqual(before,len(host.state.delayed_triggers))
