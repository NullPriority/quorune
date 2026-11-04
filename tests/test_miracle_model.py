"""CR 121.9/400.7/702.94: fixed costs and distinct as-drawn/hand roles."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from quorune.drawing.miracle_model import (
    FixedMiracleCost, MiracleDrawOpportunity, MiracleModelError, MiracleRevealedHand,
)
from quorune.drawing.miracle_continuation import MiracleRevealContinuation
from quorune.drawing.continuation import DrawResume, DrawRevealDecisionContinuation
from quorune.drawing.model import (
    DrawError, DrawEventRequest, DiscardDrawnCardUnlessType, RevealDrawnCardBySource,
)
from quorune.model import CardInstance, StackItem
from quorune.drawing.miracle_triggers import (
    MIRACLE_HAND_CONTEXT_FIELD, miracle_reveal_active, revealed_hand_for_trigger,
)
from quorune.trigger_batches import create_pending_trigger_batch
from quorune.replacement import PreventDraw, ReplacementClass, ReplacementEffect
from quorune.semantic_runtime.draw_reveals import DrawRevealPolicy
from quorune.semantic_runtime.draw_reveals import default_draw_reveal_registry
from quorune.semantic_runtime.intrinsic_draw_reveals import (
    MiracleIntrinsicRevealHandler, miracle_reveal_descriptor,
)
from quorune.semantic_runtime.context import SemanticNodeError


class MiracleModelTests(unittest.TestCase):
    @staticmethod
    def card():
        return CardInstance(object_id='card-1',ref='P1',oracle_id='fixture:miracle',
            printed_name='Generic Miracle Witness',owner='A',controller='A',zone='library',
            zone_change_counter=3)

    def opportunity(self, card=None):
        return MiracleDrawOpportunity.capture(card or self.card(),event_id='draw-1',drawer='A',
            program_key='fixture:miracle:static:front:n1',cost=FixedMiracleCost.from_cost_text('{1}{R}'),
            turn_sequence=4,turn_anchor='B',library_size=20,draw_ordinal=1)

    def test_fixed_mana_matches_normal_vector_and_copies_serialized_values(self):
        zero=FixedMiracleCost.from_cost_text('{0}')
        self.assertTrue(all(value==0 for value in zero.mana_cost.values()))
        cost=FixedMiracleCost.from_cost_text('{2}{G}{C}')
        self.assertEqual((2,1,1),(cost.mana_cost['GENERIC'],cost.mana_cost['G'],cost.mana_cost['C']))
        value=cost.to_dict()
        self.assertEqual(cost,FixedMiracleCost.from_dict(value))
        copied=FixedMiracleCost.from_dict(value)
        value['mana_cost']['G']=99
        self.assertEqual(1,copied.mana_cost['G'])
        value=copied.to_dict();value['mana_cost']['C']=8
        self.assertEqual(1,copied.mana_cost['C'])

    def test_unknown_costs_versions_and_mismatched_vectors_fail_closed(self):
        for text in ('','{X}{R}','{R/G}','{S}','{R/P}','{01}','{R} text',' {R}','{r}'):
            with self.subTest(text=text),self.assertRaises(MiracleModelError):
                FixedMiracleCost.from_cost_text(text)
        value=FixedMiracleCost.from_cost_text('{R}').to_dict()
        for changed in ({'schema_version':True},{'schema_version':2},{'extra':1},{'mana_cost':None}):
            with self.subTest(changed=changed),self.assertRaises(MiracleModelError):
                FixedMiracleCost.from_dict({**value,**changed})
        for replacement in (False,-1,0,1.0):
            bad=deepcopy(value);bad['mana_cost']['R']=replacement
            with self.subTest(replacement=replacement),self.assertRaises(MiracleModelError):
                FixedMiracleCost.from_dict(bad)

    def test_drawer_is_distinct_from_turn_anchor_and_current_draw_facts_are_checked(self):
        card=self.card();opportunity=self.opportunity(card)
        current=dict(drawer='A',turn_sequence=4,turn_anchor='B',library_size=20,draw_ordinal=1,top_object_id=card.object_id)
        opportunity.require_current_library(card,**current)
        self.assertEqual('B',opportunity.turn_anchor)
        self.assertEqual('A',opportunity.drawer)
        self.assertEqual(opportunity,MiracleDrawOpportunity.from_dict(opportunity.to_dict()))
        for change in ({'drawer':'C'},{'turn_sequence':5},{'turn_anchor':'C'},
                       {'library_size':19},{'draw_ordinal':2},{'draw_ordinal':True},{'top_object_id':'other'}):
            with self.subTest(change=change),self.assertRaises(MiracleModelError):
                opportunity.require_current_library(card,**{**current,**change})
        card.zone_change_counter+=1
        with self.assertRaises(MiracleModelError):opportunity.require_current_library(card,**current)

    def test_hand_binding_uses_new_logical_identity_and_does_not_store_a_caster(self):
        card=self.card();opportunity=self.opportunity(card)
        old_identity=card.logical_object_id
        card.zone='hand';card.zone_change_counter+=1;card.ref='P2'
        hand=opportunity.bind_revealed_hand(card,reveal_trigger_ref='S1')
        self.assertNotEqual(old_identity,hand.hand_logical_object_id)
        self.assertEqual(card.logical_object_id,hand.hand_logical_object_id)
        self.assertEqual('P2',hand.hand_ref)
        self.assertEqual('S1',hand.reveal_trigger_ref)
        self.assertNotIn('caster',hand.to_dict())
        hand.require_current_hand(card)
        self.assertEqual(hand,MiracleRevealedHand.from_dict(hand.to_dict()))
        card.zone_change_counter+=2
        with self.assertRaises(MiracleModelError):hand.require_current_hand(card)

    def test_wrong_card_owner_zone_or_noncard_cannot_be_the_committed_draw(self):
        card=self.card();opportunity=self.opportunity(card)
        for wrong in (replace(card,zone='hand'),replace(card,owner='B'),replace(card,is_token=True)):
            with self.subTest(wrong=wrong.zone),self.assertRaises(MiracleModelError):self.opportunity(wrong)
        for wrong in (replace(card,zone='hand',zone_change_counter=5),
                      replace(card,zone='hand',zone_change_counter=4,owner='B'),
                      replace(card,zone='hand',zone_change_counter=4,object_id='other')):
            with self.assertRaises(MiracleModelError):opportunity.bind_revealed_hand(wrong,reveal_trigger_ref='S1')

    def test_serialized_roles_first_ordinal_and_complete_shapes_are_closed(self):
        card=self.card();opportunity=self.opportunity(card)
        before=opportunity.to_dict()
        for change in ({'schema_version':True},{'draw_ordinal':2},{'draw_ordinal':True},
                       {'library_size':0},{'turn_sequence':-1},{'library_zone_change_counter':False},
                       {'kind':'revealed_hand'},{'extra':0}):
            with self.subTest(change=change),self.assertRaises(MiracleModelError):
                MiracleDrawOpportunity.from_dict({**before,**change})
        card.zone='hand';card.zone_change_counter+=1
        hand=opportunity.bind_revealed_hand(card,reveal_trigger_ref='S1').to_dict()
        with self.assertRaises(MiracleModelError):MiracleDrawOpportunity.from_dict(hand)
        with self.assertRaises(MiracleModelError):MiracleRevealedHand.from_dict(before)
        del hand['reveal_trigger_ref']
        with self.assertRaises(MiracleModelError):MiracleRevealedHand.from_dict(hand)


class MiracleContinuationTests(unittest.TestCase):
    card = staticmethod(MiracleModelTests.card)
    opportunity = MiracleModelTests.opportunity

    def continuation(self):
        opportunity = self.opportunity()
        mandatory = DrawRevealPolicy('mandatory', 'source-1', 'P3', 'source-1@0', 0, 'B', 'A', False)
        optional = DrawRevealPolicy('optional', 'source-2', 'P4', 'source-2@0', 0, 'C', 'A', True)
        request = DrawEventRequest(
            event_id=opportunity.event_id, player='A', library_size=20,
            reason='enclosing two-card draw', private=True,
            post_draw_actions=(RevealDrawnCardBySource('source-1', 'P3', 'source-1@0', 0),
                               DiscardDrawnCardUnlessType('land')),
        )
        return MiracleRevealContinuation(opportunity, request, (), (), 2,
                                         DrawResume.none(), (DiscardDrawnCardUnlessType('land'),),
                                         (mandatory, optional))

    def test_pending_intrinsic_reveal_preserves_draw_tail_and_prior_source_choices(self):
        continuation = self.continuation()
        serialized = continuation.to_dict()
        decoded = MiracleRevealContinuation.from_dict(serialized)
        self.assertEqual(continuation, decoded)
        prepared = decoded.prepare(apnap_order=('B', 'C', 'A'))
        self.assertEqual('draw', prepared.resolution.kind)
        self.assertEqual(continuation.request.post_draw_actions, prepared.resolution.post_draw_actions)
        self.assertEqual(2, decoded.remaining_draws)
        self.assertEqual((DiscardDrawnCardUnlessType('land'),), decoded.instruction_post_draw_actions)
        self.assertEqual(('B', 'C'), tuple(policy.source_controller for policy in decoded.source_policies))
        serialized['request']['post_draw_actions'].clear()
        serialized['source_policies'][0]['source_ref'] = 'forged'
        self.assertEqual(2, len(decoded.request.post_draw_actions))
        self.assertEqual('P3', decoded.source_policies[0].source_ref)
        with self.assertRaises(DrawError):
            DrawRevealDecisionContinuation.from_dict(decoded.to_dict())

    def test_crosswired_instruction_and_source_policy_changes_fail_closed(self):
        continuation = self.continuation()
        for request_change in ({'event_id':'other'}, {'player':'B'}, {'library_size':19}):
            with self.subTest(request_change=request_change), self.assertRaises(MiracleModelError):
                replace(continuation, request=replace(continuation.request, **request_change))
        for policies in ((), (continuation.source_policies[0],)*2,
                         (replace(continuation.source_policies[0], player='B'),),
                         (replace(continuation.source_policies[0], source_ref='stale'),)):
            with self.subTest(policies=policies), self.assertRaises(MiracleModelError):
                replace(continuation, source_policies=policies)
        with self.assertRaises(MiracleModelError):
            replace(continuation, request=replace(continuation.request, post_draw_actions=()))
        with self.assertRaises(MiracleModelError):
            replace(continuation, request=replace(continuation.request,
                    post_draw_actions=(continuation.request.post_draw_actions[0],)*2))
        with self.assertRaises(MiracleModelError):
            replace(continuation, instruction_post_draw_actions=())
        with self.assertRaises(MiracleModelError):
            replace(continuation, instruction_post_draw_actions=continuation.request.post_draw_actions)

    def test_replacement_reconstruction_cannot_promote_a_nondraw_result(self):
        continuation = self.continuation()
        effect = ReplacementEffect(
            effect_id='prevent:miracle-control', source_id='fixture:prevention',
            event_kind='draw', replacement_class=ReplacementClass.OTHER,
            conditions={'is_draw': {'eq': True}}, operations=(PreventDraw(),),
        )
        nondraw = replace(continuation, effects=(effect,))
        with self.assertRaises(DrawError):
            nondraw.prepare(apnap_order=('B', 'C', 'A'))

    def test_unknown_or_untyped_saved_state_is_rejected(self):
        value = self.continuation().to_dict()
        for change in ({'schema_version':True}, {'schema_version':2}, {'kind':'draw.reveal'},
                       {'remaining_draws':0}, {'remaining_draws':True}, {'effects':{}},
                       {'journal':[{}]}, {'source_policies':[{}]}, {'extra':0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                MiracleRevealContinuation.from_dict({**value, **change})
        for field in value:
            bad = deepcopy(value)
            del bad[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                MiracleRevealContinuation.from_dict(bad)


class MiracleDescriptorTests(unittest.TestCase):
    def test_intrinsic_descriptor_uses_existing_reveal_registry_and_closed_fixed_cost(self):
        cost = FixedMiracleCost.from_cost_text('{W}{B}')
        descriptor = miracle_reveal_descriptor(cost)
        registry = default_draw_reveal_registry()
        registry.validate(descriptor)
        self.assertEqual((cost,), registry.lower(descriptor, None))
        self.assertTrue(descriptor['requires_complete_card_program'])
        descriptor['cost']['mana_cost']['W'] = 99
        self.assertEqual(1, cost.mana_cost['W'])

    def test_intrinsic_descriptor_rejects_wrong_roles_admission_and_malformed_cost(self):
        descriptor = miracle_reveal_descriptor(FixedMiracleCost.from_cost_text('{R}'))
        handler = MiracleIntrinsicRevealHandler()
        for change in ({'schema_version':True}, {'schema_version':2}, {'event':'draw.reveal_as_drawn'},
                       {'handler_id':'action.draw.reveal-first.v1'}, {'requires_complete_card_program':False},
                       {'cost':None}, {'extra':0}):
            with self.subTest(change=change), self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, **change})
        for field in descriptor:
            bad = deepcopy(descriptor)
            del bad[field]
            with self.subTest(missing=field), self.assertRaises(SemanticNodeError):
                handler.validate(bad)


class MiracleRevealLifetimeTests(unittest.TestCase):
    def setup_card_and_trigger(self):
        card = MiracleModelTests.card()
        opportunity = MiracleModelTests().opportunity(card)
        card.zone = 'hand'; card.zone_change_counter += 1; card.ref = 'P2'
        card.known_to = ['A', 'B', 'C', 'D']
        card.revealed_to = ['C']  # An independent disclosure must remain intact.
        hand = opportunity.bind_revealed_hand(card, reveal_trigger_ref='S1')
        trigger = StackItem(stack_id='stack-1', ref='S1', kind='triggered_ability', controller='A',
                            label='Miracle trigger', source_object_id=card.object_id,
                            context={MIRACLE_HAND_CONTEXT_FIELD: hand.to_dict()})
        return card, trigger

    def test_only_original_live_or_pending_trigger_sustains_intrinsic_reveal(self):
        card, trigger = self.setup_card_and_trigger()
        state = SimpleNamespace(stack=[trigger], pending_trigger_batches=[])
        self.assertTrue(miracle_reveal_active(state, card))
        batch = create_pending_trigger_batch(batch_id='batch-1',ref='TB1',items=[trigger.to_dict()],
                                             apnap_order=('B','C','D','A'),turn_sequence=4,priority_epoch=1)
        state.stack.clear(); state.pending_trigger_batches.append(batch)
        self.assertTrue(miracle_reveal_active(state, card))
        state.pending_trigger_batches.clear()
        self.assertFalse(miracle_reveal_active(state, card))
        self.assertEqual(['C'], card.revealed_to)
        self.assertEqual(['A','B','C','D'], card.known_to)

    def test_copied_trigger_keeps_linked_subject_but_cannot_extend_original_disclosure(self):
        card, trigger = self.setup_card_and_trigger()
        copied = replace(trigger, stack_id='stack-2',ref='S2',controller='B',context=deepcopy(trigger.context))
        state = SimpleNamespace(stack=[trigger,copied],pending_trigger_batches=[])
        self.assertTrue(miracle_reveal_active(state, card))
        state.stack.remove(trigger)
        self.assertFalse(miracle_reveal_active(state, card))
        bound = revealed_hand_for_trigger(copied)
        self.assertEqual('A', bound.drawer)
        self.assertEqual('B', copied.controller)
        self.assertEqual('S1', bound.reveal_trigger_ref)
        bound.require_current_hand(card)
        self.assertEqual(['C'], card.revealed_to)

    def test_leaving_and_returning_hand_does_not_restore_original_disclosure(self):
        card, trigger = self.setup_card_and_trigger()
        state = SimpleNamespace(stack=[trigger],pending_trigger_batches=[])
        card.zone='exile'
        self.assertFalse(miracle_reveal_active(state, card))
        card.zone='hand';card.zone_change_counter += 2
        self.assertFalse(miracle_reveal_active(state, card))
