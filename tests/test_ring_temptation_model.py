from dataclasses import FrozenInstanceError, replace
import json
import unittest

from quorune.ring_model import (
    RingBearerChoice, RingBearerChoicePlan, RingBearerSnapshot, RingEmblemState,
    RingObjectIdentity, RingStateError,
)
from quorune.source_continuity import SourceContinuitySnapshot


class RingTemptationModelTests(unittest.TestCase):
    def setUp(self):
        self.emblem = RingObjectIdentity('emblem:A', 'E1', 'emblem:A:0')
        self.history = SourceContinuitySnapshot(0, 0, 0)
        self.bearer = RingBearerSnapshot(
            RingObjectIdentity('creature:one', 'C1', 'creature:one:0'), self.history)
        self.other = RingBearerSnapshot(
            RingObjectIdentity('creature:two', 'C2', 'creature:two:0'), self.history)

    def test_progress_retains_actual_count_and_stops_at_four_abilities(self):
        state = RingEmblemState('A', self.emblem, 1, self.bearer)
        for count in range(1, 8):
            self.assertEqual(count, state.temptation_count)
            self.assertEqual(min(count, 4), state.ability_level)
            self.assertEqual(self.emblem, state.emblem)
            self.assertEqual(self.bearer, state.bearer)
            state = state.tempted_again()
        with self.assertRaises(FrozenInstanceError):
            state.temptation_count = 1
        cleared = state.without_bearer()
        self.assertEqual(state.temptation_count, cleared.temptation_count)
        self.assertIsNone(cleared.bearer)

    def test_bearer_choice_is_required_and_incarnation_sealed(self):
        state = RingEmblemState('A', self.emblem, 2, self.bearer)
        plan = RingBearerChoicePlan(state, (self.bearer, self.other))
        self.assertTrue(plan.needs_choice)
        with self.assertRaises(RingStateError):
            plan.select(None)
        with self.assertRaises(RingStateError):
            plan.select(replace(self.other, identity=replace(
                self.other.identity, logical_object_id='creature:two:1')))
        same = plan.select(self.bearer)
        self.assertTrue(same.creature_chosen)
        self.assertEqual(state, same.state)
        changed = plan.select(self.other)
        self.assertTrue(changed.creature_chosen)
        self.assertEqual(self.other, changed.state.bearer)
        self.assertEqual(self.bearer, state.bearer)

    def test_impossible_choice_preserves_designation_without_claiming_a_choice(self):
        # A phased-out old bearer need not be an available creature choice.
        for bearer in (None, self.bearer):
            with self.subTest(bearer=bearer):
                state = RingEmblemState('A', self.emblem, 3, bearer)
                plan = RingBearerChoicePlan(state, ())
                self.assertFalse(plan.needs_choice)
                result = plan.select(None)
                self.assertFalse(result.creature_chosen)
                self.assertEqual(state, result.state)
                with self.assertRaises(RingStateError):
                    plan.select(self.other)

    def test_pending_choice_codec_is_exact_and_detached_from_input(self):
        state = RingEmblemState('A', self.emblem, 4, self.bearer)
        original = RingBearerChoicePlan(state, (self.bearer, self.other))
        serialized = json.loads(json.dumps(original.to_dict()))
        decoded = RingBearerChoicePlan.from_dict(serialized)
        self.assertEqual(original, decoded)
        serialized['state']['emblem']['logical_object_id'] = 'different-emblem'
        serialized['candidates'][0]['identity']['object_ref'] = 'different-ref'
        self.assertEqual(original, decoded)
        self.assertEqual(self.other, decoded.select(self.other).state.bearer)

    def test_malformed_state_and_choice_fields_reject(self):
        state = RingEmblemState('A', self.emblem, 1)
        for count in (0, -1, True, 1.0, '1', None):
            with self.subTest(count=count), self.assertRaises(RingStateError):
                replace(state, temptation_count=count)
        for version in (True, 0, 2, '1'):
            with self.subTest(version=version), self.assertRaises(RingStateError):
                RingEmblemState.from_dict({**state.to_dict(), 'schema_version': version})
        for identity in ({'object_id':'x'}, {**self.bearer.identity.to_dict(), 'unknown':True},
                         {**self.bearer.identity.to_dict(), 'object_ref':''}):
            with self.subTest(identity=identity), self.assertRaises(RingStateError):
                RingObjectIdentity.from_dict(identity)
        for value in ({**state.to_dict(), 'unknown':True},
                      {k:v for k,v in state.to_dict().items() if k!='bearer'}):
            with self.subTest(state=value), self.assertRaises(RingStateError):
                RingEmblemState.from_dict(value)
        for candidates in ([self.bearer], (self.bearer, self.bearer),
                           (RingBearerSnapshot(self.emblem, self.history),),
                           (self.bearer, replace(self.other, identity=replace(
                               self.other.identity, object_ref=self.bearer.identity.object_ref)))):
            with self.subTest(candidates=candidates), self.assertRaises(RingStateError):
                RingBearerChoicePlan(state, candidates)
        with self.assertRaises(RingStateError):
            replace(state, bearer=RingBearerSnapshot(self.emblem, self.history))
        plan = RingBearerChoicePlan(state, (self.bearer,)).to_dict()
        with self.assertRaises(RingStateError):
            RingBearerChoicePlan.from_dict({**plan, 'candidates': tuple(plan['candidates'])})
        with self.assertRaises(RingStateError):
            RingBearerChoicePlan.from_dict({**plan, 'unknown':True})
        with self.assertRaises(RingStateError):
            RingBearerChoice(state, self.other)

    def test_control_continuity_ignores_phasing_and_untapping(self):
        self.assertTrue(self.bearer.control_is_continuous(SourceContinuitySnapshot(0, 2, 4)))
        self.assertFalse(self.bearer.control_is_continuous(SourceContinuitySnapshot(2, 0, 0)))
        with self.assertRaises(RingStateError):
            self.bearer.control_is_continuous(None)
        malformed = self.bearer.to_dict()
        malformed['history']['control_epoch'] = True
        with self.assertRaises(RingStateError):
            RingBearerSnapshot.from_dict(malformed)
