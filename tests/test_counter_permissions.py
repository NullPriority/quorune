from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.compiler.counter_permission_templates import fixed_counter_prohibition
from quorune.ability_fragments import (
    ability_fragment_from_dict, ability_fragment_to_dict, canonical_ability_fragments,
)
from quorune.counter_permission import (
    CounterPermissionError, CounterPermissionSnapshot, CounterPlacementFact,
    CounterProhibitionRule, FixedCounterProhibitionSpec,
)


class CounterPermissionModelTests(unittest.TestCase):
    def fact(self, **changes):
        fields=dict(subject_kind='permanent', subject_id='object', counter_name='+1/+1',
            amount=1, controller='A', zone='battlefield', types=('creature',))
        fields.update(changes)
        return CounterPlacementFact(**fields)

    def test_complete_static_source_clauses_lower_to_fixed_public_predicates(self):
        cases=(
            ("This creature can't have counters put on it.", FixedCounterProhibitionSpec('permanent','self')),
            ("Example Sentinel can't have counters put on it.", FixedCounterProhibitionSpec('permanent','self')),
            ("You can't get poison counters.", FixedCounterProhibitionSpec('player','you','poison')),
            ("Players can't get counters.", FixedCounterProhibitionSpec('player','any')),
            ("Creatures you control can't have -1/-1 counters put on them.",
                FixedCounterProhibitionSpec('permanent','you','-1/-1',('creature',))),
            ("Creatures your opponents control can't have +1/+1 counters put on them.",
                FixedCounterProhibitionSpec('permanent','opponent','+1/+1',('creature',))),
            ("Counters can't be put on artifacts, creatures, enchantments, or lands.",
                FixedCounterProhibitionSpec('permanent','any',None,('artifact','creature','enchantment','land'))))
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, fixed_counter_prohibition(text,source_name='Example Sentinel'))
                self.assertEqual(expected, FixedCounterProhibitionSpec.from_dict(expected.to_dict()))

    def test_permission_is_a_typed_copiable_fragment_separate_from_counter_maximums(self):
        from quorune.counter_maximums import CounterMaximumSpec
        from quorune.compiler.counter_permission_templates import static_counter_prohibition_handler
        from quorune.semantic_runtime.counter_permissions import CounterProhibitionAbilityFragmentHandler
        spec = FixedCounterProhibitionSpec('permanent', 'self')
        encoded = ability_fragment_to_dict(spec)
        self.assertEqual('counter_prohibition', encoded['kind'])
        self.assertEqual(spec, ability_fragment_from_dict(encoded))
        self.assertEqual((spec,), canonical_ability_fragments([encoded]))
        self.assertNotEqual(ability_fragment_to_dict(CounterMaximumSpec('+1/+1', 0)), encoded)
        _template, descriptor, _cap = static_counter_prohibition_handler(
            "This creature can't have counters put on it.", source_name='Example Sentinel')
        self.assertEqual(spec, CounterProhibitionAbilityFragmentHandler().validate(descriptor))

    def test_current_controller_and_counter_kind_keep_player_and_creature_scopes_distinct(self):
        player=FixedCounterProhibitionSpec('player','you','poison')
        self.assertTrue(player.blocks(self.fact(subject_kind='player',subject_id='B',counter_name='poison',
            controller=None,zone=None,types=()),source_object_id='source',source_controller='B'))
        self.assertFalse(player.blocks(self.fact(subject_kind='player',subject_id='A',counter_name='poison',
            controller=None,zone=None,types=()),source_object_id='source',source_controller='B'))
        self.assertFalse(player.blocks(self.fact(subject_kind='player',subject_id='B',counter_name='energy',
            controller=None,zone=None,types=()),source_object_id='source',source_controller='B'))
        opposing=FixedCounterProhibitionSpec('permanent','opponent','+1/+1',('creature',))
        for seat in 'ACD':
            self.assertTrue(opposing.blocks(self.fact(controller=seat),source_object_id='source',source_controller='B'))
        self.assertFalse(opposing.blocks(self.fact(controller='B'),source_object_id='source',source_controller='B'))
        self.assertFalse(opposing.blocks(self.fact(counter_name='-1/-1'),source_object_id='source',source_controller='B'))

    def test_permanent_type_union_and_zone_boundaries_do_not_cover_unrelated_subjects(self):
        spec=FixedCounterProhibitionSpec('permanent','any',None,('artifact','creature','enchantment','land'))
        for types in (('artifact',),('land',),('creature','planeswalker')):
            self.assertTrue(spec.blocks(self.fact(types=types),source_object_id='source',source_controller='A'))
        for types in (('planeswalker',),('battle',),()):
            self.assertFalse(spec.blocks(self.fact(types=types),source_object_id='source',source_controller='A'))
        self.assertFalse(spec.blocks(self.fact(zone='exile',controller=None,types=('artifact',)),
            source_object_id='source',source_controller='A'))
        self.assertFalse(spec.blocks(self.fact(subject_kind='player',subject_id='B',controller=None,
            zone=None,types=()),source_object_id='source',source_controller='A'))

    def test_self_identity_does_not_require_keeping_printed_creature_type_and_zero_is_no_placement(self):
        spec=FixedCounterProhibitionSpec('permanent','self')
        rule=CounterProhibitionRule(spec,'object','B')
        snapshot=CounterPermissionSnapshot((rule,))
        self.assertFalse(snapshot.allows(self.fact(controller='A',types=('artifact',))))
        self.assertTrue(snapshot.allows(self.fact(subject_id='another')))
        self.assertTrue(snapshot.allows(self.fact(amount=0)))
        self.assertTrue(snapshot.allows(self.fact(zone='hand',controller=None)))
        self.assertTrue(CounterPermissionSnapshot().allows(self.fact()))

    def test_unknown_metadata_and_malformed_primitive_facts_are_rejected(self):
        spec=FixedCounterProhibitionSpec('permanent','self')
        before=deepcopy(spec.to_dict())
        for field,value in (('schema_version',True),('schema_version',2),('future',True),
                ('relation','chosen_player'),('target_kind',[]),('types_any',['creature']),
                ('types_any',['unknown']),('counter_name',True)):
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                FixedCounterProhibitionSpec.from_dict({**before,field:value})
        for changes in (dict(amount=True),dict(amount=-1),dict(subject_kind=[]),
                dict(subject_id=''),dict(controller=None),dict(zone=None),dict(types='creature')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.fact(**changes)
        self.assertEqual(before,spec.to_dict())
        with self.assertRaises(CounterPermissionError):
            CounterPermissionSnapshot([CounterProhibitionRule(spec,'source','A')])

    def test_open_conditions_durations_antecedents_and_coupled_instructions_stay_unrecognized(self):
        for text in (
                "That creature can't have counters put on it.",
                "Enchanted creature can't become untapped and can't have counters put on it.",
                "That player can't get counters for as long as this creature remains on the battlefield.",
                "You can't get additional poison counters this turn.",
                "As long as you control an artifact, players can't get counters.",
                "Players can't get counters. Draw a card.",
                "Counters can't be put on cards in graveyards."):
            with self.subTest(text=text):
                self.assertIsNone(fixed_counter_prohibition(text,source_name='Example Sentinel'))


class CounterPermissionEventTests(unittest.TestCase):
    def event(self, *, kind='permanent', zone='battlefield', name='+1/+1'):
        from quorune.replacement_effects import AffectedObject, ReplaceableEvent
        return ReplaceableEvent(event_id='counter-test', kind='counter.place',
            affected_player='B' if kind=='player' else None,
            affected_object=None if kind=='player' else AffectedObject('target','A','A'),
            payload=dict(target_kind=kind, target_zone=zone, target_controller='A',
                target_types=['creature'], counter_name=name, amount=2, requested_amount=2,
                placing_player='C', effect_generated=True))

    def permissions(self):
        return CounterPermissionSnapshot((CounterProhibitionRule(
            FixedCounterProhibitionSpec('permanent','any',None,('creature',)), 'source','D'),))

    def test_denial_is_not_a_replacement_and_seals_actual_quantity_without_mutating_input(self):
        from quorune.counter_permission_events import (
            apply_counter_permissions, counter_event_is_prohibited, COUNTER_PERMISSIONS_FIELD,
        )
        event=self.event()
        before=event.to_dict()
        denied=apply_counter_permissions(event,self.permissions())
        self.assertTrue(counter_event_is_prohibited(denied))
        self.assertEqual(0,denied.payload['amount'])
        self.assertEqual(2,denied.payload['requested_amount'])
        self.assertEqual((),denied.applied_effects)
        self.assertEqual(before,event.to_dict())
        self.assertEqual(denied,apply_counter_permissions(denied))
        self.assertEqual(self.permissions(),CounterPermissionSnapshot.from_dict(denied.payload[COUNTER_PERMISSIONS_FIELD]))

    def test_empty_policy_preserves_historical_event_bytes_and_off_battlefield_cards(self):
        from quorune.counter_permission_events import apply_counter_permissions,counter_event_is_prohibited
        event=self.event()
        self.assertIs(event,apply_counter_permissions(event,CounterPermissionSnapshot()))
        self.assertIs(event,apply_counter_permissions(event))
        for kind,zone in (('card','exile'),('permanent','hand'),('player',None)):
            placed=apply_counter_permissions(self.event(kind=kind,zone=zone),self.permissions())
            self.assertFalse(counter_event_is_prohibited(placed))
            self.assertEqual(2,placed.payload['amount'])

    def test_selected_entry_form_can_replace_own_permission_without_consuming_replacements(self):
        from quorune.counter_permission_events import apply_counter_permissions,counter_event_is_prohibited
        denied=apply_counter_permissions(self.event(),self.permissions())
        allowed=apply_counter_permissions(denied,CounterPermissionSnapshot())
        self.assertFalse(counter_event_is_prohibited(allowed))
        self.assertEqual(2,allowed.payload['amount'])
        self.assertEqual((),allowed.applied_effects)
        excluded=apply_counter_permissions(self.event(),self.permissions(),excluded_sources=('source',))
        self.assertEqual(2,excluded.payload['amount'])
        self.assertFalse(counter_event_is_prohibited(excluded))

    def test_corrupt_event_permission_markers_and_facts_fail_before_mutation(self):
        from quorune.counter_permission_events import (
            apply_counter_permissions, COUNTER_PERMISSIONS_FIELD, COUNTER_PROHIBITED_FIELD,
        )
        event=self.event()
        for field,value in (('target_types','creature'),('target_controller',False),('amount',True)):
            with self.subTest(field=field),self.assertRaises(ValueError):
                apply_counter_permissions(replace(event,payload={**dict(event.payload),field:value}),self.permissions())
        denied=apply_counter_permissions(event,self.permissions())
        payload=dict(denied.payload);payload.pop(COUNTER_PERMISSIONS_FIELD)
        with self.assertRaises(CounterPermissionError):
            apply_counter_permissions(replace(denied,payload=payload))
        with self.assertRaises(CounterPermissionError):
            apply_counter_permissions(replace(denied,payload={**dict(denied.payload),'requested_amount':None}))

    def test_authoritative_prepare_does_not_offer_replacements_for_prohibited_effects(self):
        from quorune.counter_placement import CounterPlacementError, prepare_counter_placement_specs
        from quorune.semantic_runtime.counter_replacements import CounterPlacementEventSpec
        spec=CounterPlacementEventSpec(event_id='player-effect',subject_kind='player',subject_id='B',
            placing_player='A',counter_name='energy',amount=2,source_ref=None,effect_generated=True)
        permissions=CounterPermissionSnapshot((CounterProhibitionRule(
            FixedCounterProhibitionSpec('player','any'),'source','A'),))
        with patch('quorune.semantic_runtime.counter_permissions.collect_counter_permissions',return_value=permissions), \
                patch('quorune.counter_placement.collect_counter_placement_replacement_effects',
                    side_effect=AssertionError('Impossible placement must not discover quantity choices')):
            prepared=prepare_counter_placement_specs(SimpleNamespace(),(spec,),batch_id='permission-test')
            self.assertEqual(0,prepared.events[0].payload['amount'])
            self.assertEqual((),prepared.effects)
            self.assertEqual((),prepared.journal)
            with self.assertRaises(CounterPlacementError):
                prepare_counter_placement_specs(SimpleNamespace(),(spec,),batch_id='permission-cost',for_cost=True)
            with self.assertRaises(CounterPlacementError):
                prepare_counter_placement_specs(SimpleNamespace(),(spec,),batch_id='permission-choices',selections=('stale',))

    def test_entry_child_is_blocked_before_quantity_replacement_choice(self):
        from quorune.replacement_effects import (
            ReplacementClass,ReplacementEffect,CreateAffectedObjectCounter,
        )
        from quorune.semantic_runtime.zone_replacement_model import (
            ZoneChangeReplacementSnapshot,ZoneChangeSubjectSnapshot,
        )
        from quorune.semantic_runtime.zone_replacements import prepare_zone_change_replacement_snapshot
        from quorune.counter_permission_events import counter_event_is_prohibited
        subject=ZoneChangeSubjectSnapshot(object_id='target',object_ref='public-target',
            logical_object_id='old-target',owner='A',controller='A',origin='hand',
            destination='battlefield',destination_controller='A',entry_face_id='front',
            object_types=('creature',),is_card_object=True,counter_permissions=self.permissions())
        entry=ReplacementEffect(effect_id='entry-counters',source_id='public-target',event_kind='zone.change',
            replacement_class=ReplacementClass.OTHER,conditions={'destination':{'eq':'battlefield'}},
            operations=(CreateAffectedObjectCounter(counter_name='+1/+1',amount=2,
                placing_player='A',source_ref='public-target'),))
        quantity=ReplacementEffect(effect_id='double',source_id='quantity-source',event_kind='counter.place',
            replacement_class=ReplacementClass.OTHER,conditions={},
            operations=({'op':'multiply','field':'amount','factor':2},))
        snapshot=ZoneChangeReplacementSnapshot(revision=0,event_sequence=0,apnap_order=tuple('ABCD'),
            source_refs=('source',),subjects=(subject,),effects=(entry,quantity))
        prepared=prepare_zone_change_replacement_snapshot(snapshot)['target']
        events=prepared.event.children
        self.assertEqual(1,len(events))
        self.assertTrue(counter_event_is_prohibited(events[0]))
        self.assertEqual(0,events[0].payload['amount'])
        self.assertEqual((),events[0].applied_effects)


class CounterPermissionChoiceTests(unittest.TestCase):
    def test_live_counter_prohibition_sources_keep_current_instead_of_prospective_presence(self):
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        from quorune.compiler.counter_permission_templates import static_counter_prohibition_handler
        from quorune.semantic_runtime.counter_permissions import collect_counter_permissions
        descriptor = static_counter_prohibition_handler("Players can't get counters.",source_name='Example')[1]
        program = SimpleNamespace(handlers=(descriptor,),coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,))
        source = SimpleNamespace(object_id='source',zone='battlefield',controller='A',phased_out=False,
            oracle_id='example',annotations={})
        host = SimpleNamespace(active_seats=tuple('ABCD'),
            _semantic_event_sources=lambda **kwargs:(source,),
            semantics=SimpleNamespace(runtime_handler_programs_for_oracle=lambda *args,**kwargs:(program,)),
            semantic_program_is_current_trusted=lambda p:True)
        with patch('quorune.semantic_runtime.counter_permissions.program_static_component_is_applicable',
                return_value=False) as presence:
            self.assertFalse(collect_counter_permissions(host).rules)
            presence.assert_called_once_with(host,program,source,prospective_zone=None,prospective_controller=None)
        with patch('quorune.semantic_runtime.counter_permissions.program_static_component_is_applicable',
                return_value=True) as presence:
            rules = collect_counter_permissions(host,prospective_controller='B',self_only=False).rules
            self.assertEqual('B',rules[0].source_controller)
            presence.assert_called_once_with(host,program,source,prospective_zone='battlefield',prospective_controller='B')

    def query(self, *, rules=()):
        from quorune.object_query import ObjectQueryResult
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
        rows = tuple(ObjectQueryResult(key, key, key, seat, seat, 'battlefield',
            types=('creature',), counters={'+1/+1': 1}, logical_object_id=key+'@0')
            for key, seat in (('source','A'), ('recipient','B'), ('other','C')))
        return SnapshotSemanticChoiceQuery(tuple('ABCD'), tuple('ABCD'), object_rows=rows,
            counter_permissions=CounterPermissionSnapshot(rules))

    def test_choice_query_uses_current_controller_and_player_scope(self):
        query = self.query(rules=(CounterProhibitionRule(
            FixedCounterProhibitionSpec('permanent','opponent','+1/+1',('creature',)), 'source','A'),))
        self.assertTrue(query.counter_placement_allowed('source','+1/+1',1))
        self.assertFalse(query.counter_placement_allowed('recipient','+1/+1',1))
        self.assertTrue(query.counter_placement_allowed('B','poison',1,subject_kind='player'))
        changed = replace(query, object_rows=tuple(replace(row,controller='A')
            if row.ref == 'recipient' else row for row in query.object_rows))
        self.assertTrue(changed.counter_placement_allowed('recipient','+1/+1',1))
        self.assertFalse(query.counter_placement_allowed('missing','+1/+1',1))
        with self.assertRaises(ValueError):replace(query,counter_permissions={})

    def test_graft_cannot_offer_or_resume_a_prohibited_movement(self):
        from quorune.semantic_choices.context import SemanticChoiceContext
        from quorune.semantic_choices.graft import GraftCounterMovementHandler
        from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceFrame
        query = self.query()
        handler = GraftCounterMovementHandler()
        effect = dict(op=handler.operation,player='A',from_ref='source',from_identity='source@0',
            to_ref='recipient',to_identity='recipient@0')
        context = SemanticChoiceContext('A','stack','A','Graft','source',None,'graft',1,query)
        prepared = handler.prepare(effect,context)
        self.assertIsNotNone(prepared.request)
        continuation = SemanticChoiceContinuation(handler.handler_id,1,'stack',prepared.continuation_effect,
            (),None,'Graft',SemanticChoiceFrame('graft',1,'stack',0,'A'))
        banned = self.query(rules=(CounterProhibitionRule(
            FixedCounterProhibitionSpec('permanent','self'),'recipient','B'),))
        self.assertIsNone(handler.prepare(effect,replace(context,query=banned)).request)
        self.assertFalse(handler.complete(continuation,{'choice':'move'},banned).intents)
        self.assertEqual(1,banned.object('source').counters['+1/+1'])

    def test_optional_placement_keeps_partial_legal_results_but_not_impossible_option(self):
        from quorune.semantic_choices.counter_permission_choices import optional_counter_placement_possible
        from quorune.semantic_runtime.intents import IntentPlan, PlaceCountersIntent, PlacePlayerCountersIntent
        query = self.query(rules=(CounterProhibitionRule(
            FixedCounterProhibitionSpec('permanent','self'),'recipient','B'),))
        def plan(refs):
            return IntentPlan('place_counters','generic',
                (PlaceCountersIntent('A',refs,'+1/+1',1,'optional'),))
        self.assertFalse(optional_counter_placement_possible(plan(('recipient',)),query))
        self.assertTrue(optional_counter_placement_possible(plan(('recipient','other')),query))
        banned = replace(query,counter_permissions=CounterPermissionSnapshot((CounterProhibitionRule(
            FixedCounterProhibitionSpec('player','any'),'source','A'),)))
        player_plan = IntentPlan('place_player_counters','generic',
            (PlacePlayerCountersIntent('A',('B',),'energy',1,'optional'),))
        self.assertFalse(optional_counter_placement_possible(player_plan,banned))
        self.assertTrue(optional_counter_placement_possible(player_plan,query))

    def test_optional_counter_handler_rechecks_permission_before_offer_and_resume(self):
        from quorune.semantic_choices.context import SemanticChoiceContext
        from quorune.semantic_choices.optional_counter_placement import OptionalCounterPlacementHandler
        from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceFrame
        handler = OptionalCounterPlacementHandler()
        effect = dict(op=handler.operation,player='A',effect=dict(op='place_counters',
            cards=['recipient','other'],counter='+1/+1',amount=1,source='source'))
        context = SemanticChoiceContext('A','stack','A','Optional','source',None,'optional',1,self.query())
        prepared = handler.prepare(effect,context)
        self.assertIsNotNone(prepared.request)
        continuation = SemanticChoiceContinuation(handler.handler_id,1,'stack',prepared.continuation_effect,
            (),None,'Optional',SemanticChoiceFrame('optional',1,'stack',0,'A'))
        banned = self.query(rules=(CounterProhibitionRule(
            FixedCounterProhibitionSpec('permanent','any',None,('creature',)),'source','A'),))
        self.assertIsNone(handler.prepare(effect,replace(context,query=banned)).request)
        self.assertFalse(handler.complete(continuation,{'choice':'put'},banned).prepend_effects)
        self.assertEqual(1,len(handler.complete(continuation,{'choice':'put'},context.query).prepend_effects))

    def test_descriptor_and_copy_policy_cannot_omit_capability_or_current_scope(self):
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        from quorune.compiler.counter_permission_templates import static_counter_prohibition_handler
        from quorune.counter_permission import counter_prohibition_payload_dependencies, COUNTER_PROHIBITION_CAPABILITY
        from quorune.replacement import SetField
        descriptor = static_counter_prohibition_handler("Players can't get counters.",source_name='Example')[1]
        self.assertEqual((COUNTER_PROHIBITION_CAPABILITY,),
            counter_prohibition_payload_dependencies(descriptor,coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,)))
        with self.assertRaises(CounterPermissionError):counter_prohibition_payload_dependencies(descriptor)
        for fields in ({'schema_version':True},{'event':'counter.place'},{'extra':1}):
            with self.assertRaises(ValueError):counter_prohibition_payload_dependencies(
                {**descriptor,**fields},coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,))
        operation = SetField('_counter_permissions',CounterPermissionSnapshot().to_dict()).to_dict()
        self.assertEqual((COUNTER_PROHIBITION_CAPABILITY,),counter_prohibition_payload_dependencies(operation))


if __name__=='__main__':
    unittest.main()
