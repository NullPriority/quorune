import copy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from quorune.continuous_effect_model import Layer
from quorune.control_effects import record_source_transition
from quorune.model import CardInstance, PlayerState
from quorune.ring_model import RingBearerChoicePlan, RingObjectIdentity, RingStateError
from quorune.ring_state import (
    RING_STATE_ANNOTATION, commit_ring_bearer_choice, current_ring_state,
    prepare_ring_temptation,
)


class RingOwnerHost:
    """Owned-state unit fixture, not CardProgram registration or native gameplay."""
    def __init__(self):
        self.state = SimpleNamespace(players={s:PlayerState(s,s) for s in ('A','B')}, cards={})
        self.types = {}
        self.logs = []
        self.sequence = 0

    @property
    def active_seats(self):
        return [s for s,p in self.state.players.items() if p.in_game]

    def _next_zone_timestamp(self):
        self.sequence += 1
        return self.sequence

    def _log(self, *args, **kwargs):
        self.logs.append(copy.deepcopy((args,kwargs)))

    def _effective_card_data(self, card, *, maximum_layer):
        if maximum_layer != Layer.TYPE:
            raise AssertionError('Bearer eligibility must use current layer-four types')
        return {'type_line':self.types[card.object_id]}

    def add_creature(self, ref, *, owner='A', controller='A'):
        card = CardInstance(object_id='object:'+ref, ref=ref, oracle_id='fixture:'+ref,
                            printed_name=ref, owner=owner, controller=controller, zone='battlefield')
        self.state.cards[card.object_id] = card
        self.state.players[controller].zones['battlefield'].append(card.object_id)
        self.types[card.object_id] = 'Creature'
        return card

    def create_emblem(self, owner, *, abilities, display_label, reason):
        ref = 'E'+str(self._next_zone_timestamp())
        card = CardInstance(object_id='emblem:'+ref, ref=ref, oracle_id='emblem-fixture:'+ref,
                            printed_name=display_label, owner=owner, controller=owner,
                            object_kind='emblem', zone='command',
                            annotations={'emblem_abilities':list(abilities)})
        self.state.cards[card.object_id] = card
        self.state.players[owner].zones['command'].append(card.object_id)
        return ref

    def checkpoint(self):
        return json.loads(json.dumps({
            'players':{s:p.to_dict() for s,p in self.state.players.items()},
            'cards':{i:c.to_dict() for i,c in self.state.cards.items()},
            'sequence':self.sequence,'logs':self.logs,
        }))


class RingTemptationStateOwnerTests(unittest.TestCase):
    def test_progress_increment_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.ring_model import RingEmblemState
        self.test_one_emblem_progresses_beyond_four_without_a_creature()
        with patch.object(RingEmblemState, 'tempted_again', lambda state: state), self.assertRaises(AssertionError):
            self.test_one_emblem_progresses_beyond_four_without_a_creature()

    def test_one_emblem_progresses_beyond_four_without_a_creature(self):
        host = RingOwnerHost()
        for count in range(1,7):
            plan = prepare_ring_temptation(host, player='A', reason='owned state witness')
            self.assertFalse(plan.needs_choice)
            self.assertEqual(count, plan.state.temptation_count)
            result = commit_ring_bearer_choice(host, plan, None)
            self.assertFalse(result.creature_chosen)
            self.assertEqual(count, current_ring_state(host,'A').temptation_count)
            self.assertEqual(min(count,4), result.state.ability_level)
        self.assertEqual(1,len(host.state.players['A'].zones['command']))
        self.assertIsNone(current_ring_state(host,'B'))

    def test_choice_uses_current_creatures_and_initializes_only_the_selected_history(self):
        host = RingOwnerHost()
        first = host.add_creature('C1',owner='B')
        second = host.add_creature('C2')
        foreign = host.add_creature('C3',owner='B',controller='B')
        plan = prepare_ring_temptation(host, player='A', reason='nontarget choice witness')
        self.assertEqual(['C1','C2'],[c.identity.object_ref for c in plan.candidates])
        before = host.checkpoint()
        for invalid in (None,foreign.ref,'unoffered',True,1):
            with self.subTest(invalid=invalid), self.assertRaises(RingStateError):
                commit_ring_bearer_choice(host,plan,invalid)
            self.assertEqual(before,host.checkpoint())
        self.assertIsNone(first.source_continuity)
        self.assertIsNone(second.source_continuity)
        result = commit_ring_bearer_choice(host,plan,first.ref)
        self.assertTrue(result.creature_chosen)
        self.assertEqual(first.object_id,result.state.bearer.identity.object_id)
        self.assertIsNotNone(first.source_continuity)
        self.assertIsNone(second.source_continuity)
        same = prepare_ring_temptation(host,player='A',reason='choose existing bearer')
        self.assertTrue(commit_ring_bearer_choice(host,same,first.ref).creature_chosen)

    def test_bearer_continuity_survives_phasing_and_type_loss_but_not_control_return(self):
        host = RingOwnerHost()
        card = host.add_creature('C1')
        plan = prepare_ring_temptation(host,player='A',reason='continuity witness')
        commit_ring_bearer_choice(host,plan,card.ref)
        card.phased_out = True
        record_source_transition(host,card,previous_phased_out=False)
        self.assertIsNotNone(current_ring_state(host,'A').bearer)
        none = prepare_ring_temptation(host,player='A',reason='phased bearer is not a choice')
        self.assertFalse(none.needs_choice)
        self.assertIsNotNone(commit_ring_bearer_choice(host,none,None).state.bearer)
        card.phased_out = False
        record_source_transition(host,card,previous_phased_out=True)
        host.types[card.object_id] = 'Artifact'
        self.assertIsNotNone(current_ring_state(host,'A').bearer)
        card.controller = 'B'
        record_source_transition(host,card,control_changed=True)
        card.controller = 'A'
        record_source_transition(host,card,control_changed=True)
        self.assertIsNone(current_ring_state(host,'A').bearer)

    def test_current_type_phase_control_and_incarnation_are_revalidated_before_commit(self):
        for change in ('type','phase','control','incarnation'):
            with self.subTest(change=change):
                host = RingOwnerHost()
                card = host.add_creature('C1')
                plan = prepare_ring_temptation(host,player='A',reason='stale choice witness')
                if change=='type':host.types[card.object_id]='Artifact'
                elif change=='phase':card.phased_out=True
                elif change=='control':card.controller='B'
                else:card.zone_change_counter+=1
                before = host.checkpoint()
                with self.assertRaises(RingStateError):
                    commit_ring_bearer_choice(host,plan,card.ref)
                self.assertEqual(before,host.checkpoint())

    def test_retained_bearer_does_not_return_with_a_new_zone_incarnation(self):
        host = RingOwnerHost()
        card = host.add_creature('C1')
        plan = prepare_ring_temptation(host,player='A',reason='departure witness')
        commit_ring_bearer_choice(host,plan,card.ref)
        card.zone = 'graveyard'
        card.zone_change_counter += 1
        self.assertIsNone(current_ring_state(host,'A').bearer)
        card.zone = 'battlefield'
        card.zone_change_counter += 1
        self.assertIsNone(current_ring_state(host,'A').bearer)

    def test_pending_choice_rejects_control_away_and_back_without_mutation(self):
        host = RingOwnerHost()
        card = host.add_creature('C1')
        first = prepare_ring_temptation(host,player='A',reason='initial choice')
        commit_ring_bearer_choice(host,first,card.ref)
        pending = prepare_ring_temptation(host,player='A',reason='pending choice')
        card.controller = 'B'
        record_source_transition(host,card,control_changed=True)
        card.controller = 'A'
        record_source_transition(host,card,control_changed=True)
        before = host.checkpoint()
        with self.assertRaises(RingStateError):
            commit_ring_bearer_choice(host,pending,card.ref)
        self.assertEqual(before,host.checkpoint())
        self.assertIsNone(current_ring_state(host,'A').bearer)

    def test_unknown_facts_and_duplicate_progress_fail_before_mutation(self):
        host = RingOwnerHost()
        card = host.add_creature('C1')
        host.types[card.object_id] = None
        before = host.checkpoint()
        with self.assertRaises(RingStateError):
            prepare_ring_temptation(host,player='A',reason='unknown type witness')
        self.assertEqual(before,host.checkpoint())

        host.types[card.object_id] = 'Creature'
        plan = prepare_ring_temptation(host,player='A',reason='duplicate witness')
        ref = host.create_emblem('A',abilities=('display only',),display_label='Emblem',reason='fixture')
        second = next(c for c in host.state.cards.values() if c.ref==ref)
        identity = RingObjectIdentity(second.object_id,second.ref,second.logical_object_id)
        second.annotations[RING_STATE_ANNOTATION] = replace(plan.state,emblem=identity).to_dict()
        before = host.checkpoint()
        with self.assertRaises(RingStateError):
            prepare_ring_temptation(host,player='A',reason='reject duplicate state')
        self.assertEqual(before,host.checkpoint())

    def test_duplicate_choice_references_fail_before_emblem_creation(self):
        host = RingOwnerHost()
        first = host.add_creature('C1')
        second = host.add_creature('C2')
        second.ref = first.ref
        before = host.checkpoint()
        with self.assertRaises(RingStateError):
            prepare_ring_temptation(host,player='A',reason='duplicate choice identities')
        self.assertEqual(before,host.checkpoint())

    def test_owned_state_and_pending_plan_round_trip_without_new_root_state_fields(self):
        host = RingOwnerHost()
        card = host.add_creature('C1')
        plan = prepare_ring_temptation(host,player='A',reason='pending codec witness')
        saved = host.checkpoint()
        restored = RingOwnerHost()
        restored.types = dict(host.types)
        restored.sequence = host.sequence
        restored.state.players = {s:PlayerState.from_dict(p) for s,p in saved['players'].items()}
        restored.state.cards = {i:CardInstance.from_dict(c) for i,c in saved['cards'].items()}
        pending = RingBearerChoicePlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        result = commit_ring_bearer_choice(restored,pending,card.ref)
        self.assertEqual(card.object_id,result.state.bearer.identity.object_id)
        self.assertEqual(1,current_ring_state(restored,'A').temptation_count)
