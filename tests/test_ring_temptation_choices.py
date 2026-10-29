import copy
import json
import unittest

import test_ring_temptation_state as ring_support
from quorune.replacement.immutable import FrozenMap
from quorune.ring_model import RING_TEMPTATION_OPERATION, RING_TEMPTED_EVENT
from quorune.ring_state import current_ring_state, materialized_ring_choice_plans
from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
from quorune.semantic_choices.model import SemanticChoiceContinuation, SemanticChoiceError, SemanticChoiceFrame
from quorune.semantic_choices.registry import SemanticChoiceRegistry
from quorune.semantic_choices.ring import RingTemptationChoiceHandler
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantic_runtime.intents import IntentPlan


class RingChoiceHost(ring_support.RingOwnerHost):
    def __init__(self):
        super().__init__()
        self.completed_events = []

    def _dispatch_semantic_event(self, event, details):
        self.completed_events.append((event,copy.deepcopy(details)))
        return []


class RingTemptationChoiceContractTests(unittest.TestCase):
    """Owner-level choice/intent integration; native commands have separate witnesses."""
    def setUp(self):
        self.host = RingChoiceHost()
        self.handler = RingTemptationChoiceHandler()
        self.registry = SemanticChoiceRegistry((self.handler,)).freeze()

    def query(self, effect):
        return SnapshotSemanticChoiceQuery(
            seat_order=('A','B'),active_order=('A','B'),
            ring_choice_plans=materialized_ring_choice_plans(self.host,'A',effect))

    def context(self, effect):
        return SemanticChoiceContext(actor='A',stack_ref='S1',stack_controller='A',
            stack_label='Ring choice contract',source_ref=None,card_ref=None,
            semantic_program_id='unit:ring-choice',semantic_program_version=1,query=self.query(effect))

    def apply(self, intents):
        return execute_intent_plan(self.host,IntentPlan(operation=RING_TEMPTATION_OPERATION,
            handler_id=self.handler.handler_id,intents=tuple(intents)))

    def prepared_choice(self):
        effect = FrozenMap({'op':RING_TEMPTATION_OPERATION,'player':'A'})
        progress = self.handler.prepare(effect,self.context(effect))
        self.assertIsNone(progress.request)
        self.apply(progress.preparation_intents)
        next_effect = progress.auto_continue.prepend_effects[0]
        return self.handler.prepare(next_effect,self.context(next_effect))

    def continuation(self, effect):
        value = SemanticChoiceContinuation(handler_id=self.handler.handler_id,handler_version=1,
            stack_ref='S1',effect=effect,remaining=(),destination=None,note='',
            semantic_frame=SemanticChoiceFrame('unit:ring-choice',1,'S1',0,'A'))
        _,decoded = self.registry.decode_continuation(json.loads(json.dumps(value.to_dict())))
        return decoded

    def test_progress_precedes_public_controller_choice_and_one_completed_occurrence(self):
        card = self.host.add_creature('C1',owner='B')
        prepared = self.prepared_choice()
        self.assertEqual([],self.host.completed_events)
        self.assertEqual(1,current_ring_state(self.host,'A').temptation_count)
        choice = prepared.request.choice
        self.assertEqual(('C1',),choice.legal_refs)
        self.assertEqual(('actor','any','public',1,1),
            (choice.controller_relation,choice.owner_relation,choice.visibility,choice.minimum,choice.maximum))
        self.assertNotIn('name',json.dumps(dict(prepared.request.public_context)))
        continuation = self.continuation(prepared.continuation_effect)
        complete = self.handler.complete(continuation,{'objects':['C1']},self.query(continuation.effect))
        self.apply(complete.intents)
        self.assertEqual(card.object_id,current_ring_state(self.host,'A').bearer.identity.object_id)
        self.assertEqual(1,len(self.host.completed_events))
        event,details = self.host.completed_events[0]
        self.assertEqual(RING_TEMPTED_EVENT,event)
        self.assertTrue(details['creature_chosen'])
        self.assertEqual(card.logical_object_id,details['chosen_bearer_logical_object_id'])
        self.assertEqual('Ring choice contract',details['reason'])

    def test_no_creature_and_choosing_existing_bearer_are_distinct_completed_facts(self):
        empty = self.prepared_choice()
        self.assertIsNone(empty.request)
        self.apply(empty.preparation_intents)
        self.assertFalse(self.host.completed_events[-1][1]['creature_chosen'])
        self.host.add_creature('C1')
        for count in (2,3):
            prepared = self.prepared_choice()
            continuation = self.continuation(prepared.continuation_effect)
            self.apply(self.handler.complete(continuation,{'objects':['C1']},self.query(continuation.effect)).intents)
            self.assertTrue(self.host.completed_events[-1][1]['creature_chosen'])
            self.assertEqual(count,self.host.completed_events[-1][1]['temptation_count'])
        self.assertEqual(3,len(self.host.completed_events))

    def test_pending_codec_rejects_response_and_progress_drift_without_mutation(self):
        self.host.add_creature('C1')
        prepared = self.prepared_choice()
        continuation = self.continuation(prepared.continuation_effect)
        before = self.host.checkpoint()
        for response in ({'objects':[]},{'objects':['C1','C1']},{'objects':'C1'},
                         {'objects':['unknown']},{'objects':[True]}, {'objects':['C1'],'player':'B'}):
            with self.subTest(response=response),self.assertRaises(SemanticChoiceError):
                self.handler.complete(continuation,response,self.query(continuation.effect))
            self.assertEqual(before,self.host.checkpoint())
        altered = copy.deepcopy(continuation.to_dict())
        altered['effect']['_ring_plan']['state']['temptation_count'] += 1
        _,wrong = self.registry.decode_continuation(altered)
        with self.assertRaises(SemanticChoiceError):
            self.handler.complete(wrong,{'objects':['C1']},self.query(wrong.effect))
        self.assertEqual(before,self.host.checkpoint())

    def test_impossible_choice_retains_a_bearer_role_without_a_chosen_creature_role(self):
        card = self.host.add_creature('C1')
        prepared = self.prepared_choice()
        continuation = self.continuation(prepared.continuation_effect)
        self.apply(self.handler.complete(continuation,{'objects':['C1']},self.query(continuation.effect)).intents)
        card.phased_out = True
        no_choice = self.prepared_choice()
        self.apply(no_choice.preparation_intents)
        details = self.host.completed_events[-1][1]
        self.assertFalse(details['creature_chosen'])
        self.assertIsNone(details['chosen_bearer'])
        self.assertEqual(card.ref,details['ring_bearer'])
        self.assertEqual(card.logical_object_id,details['ring_bearer_logical_object_id'])

    def test_stale_incarnation_rejects_and_empty_refresh_can_complete_impossible_choice(self):
        card = self.host.add_creature('C1')
        prepared = self.prepared_choice()
        continuation = self.continuation(prepared.continuation_effect)
        card.zone_change_counter += 1
        with self.assertRaises(SemanticChoiceError):
            self.handler.complete(continuation,{'objects':['C1']},self.query(continuation.effect))
        card.phased_out = True
        refreshed = self.handler.prepare(continuation.effect,self.context(continuation.effect))
        self.assertIsNone(refreshed.request)
        self.apply(refreshed.preparation_intents)
        self.assertFalse(self.host.completed_events[-1][1]['creature_chosen'])

    def test_malformed_stage_actor_and_retained_fields_fail_before_intents(self):
        for patch in ({'extra':True},{'player':'B'},{'_ring_stage':[]},
                      {'_ring_stage':True},{'_ring_stage':'unknown'}):
            effect = {'op':RING_TEMPTATION_OPERATION,'player':'A',**patch}
            with self.subTest(effect=effect),self.assertRaises((SemanticChoiceError,ValueError)):
                self.handler.prepare(effect,self.context(effect))
        self.assertEqual(0,len(self.host.state.players['A'].zones['command']))
        self.host.add_creature('C1')
        prepared = self.prepared_choice()
        malformed = dict(prepared.continuation_effect)
        malformed['_ring_plan'] = {'schema_version':2}
        with self.assertRaises(SemanticChoiceError):
            self.handler.prepare(malformed,self.context(malformed))

    def test_query_materialization_is_detached_and_normal_effects_have_no_ring_facts(self):
        self.assertEqual({},materialized_ring_choice_plans(self.host,'A',{'op':'draw'}))
        self.host.add_creature('C1')
        prepared = self.prepared_choice()
        raw = materialized_ring_choice_plans(self.host,'A',prepared.continuation_effect)
        query = SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'),ring_choice_plans=raw)
        raw['A']['candidates'][0]['identity']['object_ref'] = 'forged'
        self.assertEqual('C1',query.ring_choice_plan('A').candidates[0].identity.object_ref)
