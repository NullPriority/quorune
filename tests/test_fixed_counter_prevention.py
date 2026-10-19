from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.damage import damage_proposal, prepare_damage_batch, commit_prepared_damage_batch
from quorune.engine import TURN_STEPS
from quorune.model import CombatState
from quorune.replacement_effects import ReplacementChoiceRequired
from quorune.damage_modifier_state import (
    DamagePreventionShield, DamageSubject, PreventionMode, DamageModifierDuration,
    DealDamagePreventionAftermath, DamageAftermathRecipient,
    PlaceCountersPreventionAftermath,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.counter_prevention import FixedCounterPreventionHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectDuration,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from quorune.continuous_effect_state import commit_continuous_effect
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_spree_mode_costs as spree


TEXT = 'If damage would be dealt to this creature, prevent that damage. Remove a +1/+1 counter from this creature.'
CONDITIONAL = 'If damage would be dealt to this creature while it has a +1/+1 counter on it, prevent that damage and remove a +1/+1 counter from it.'
CAP = 'damage.prevention.fixed_self_counter_removal'


class FixedCounterPreventionCompilerTests(unittest.TestCase):
    def compile(self, text=TEXT):
        return compile_oracle_card(replace(record(text, 'Creature — Spirit'), name='Fixture'),
            capability_registry=load_default_capability_registry(), capability_profile='commander_review')

    def test_closed_source_conditions_and_counter_result_require_the_existing_removal_owner(self):
        for text, conditional in ((TEXT, False), (CONDITIONAL, True),
                (TEXT.replace('this creature', 'Fixture'), False)):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                self.assertEqual(conditional, node.handlers[0]['require_counter'])
                self.assertIn(CAP, node.capability_dependencies)
                self.assertIn('counter.removal.fixed_effect', node.capability_dependencies)

    def test_model_closes_malformed_unknown_and_amount_based_counter_variants(self):
        descriptor = {'handler_id': 'prevention.damage.fixed-self-counter.v1', 'schema_version': 1,
            'event': 'damage', 'require_counter': False}
        handler = FixedCounterPreventionHandler()
        for values in ({'schema_version': True}, {'require_counter': 1}, {'event': 'zone.change'}, {'other': True}):
            with self.subTest(values=values), self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, **values})
        for identity in (None, (), ('id',), ('id', 'id@0', True), ('id', 'id@0', -1)):
            with self.subTest(identity=identity), self.assertRaises(SemanticNodeError):
                handler.lower(descriptor, SimpleNamespace(source_identity=identity))
        for text in (TEXT.replace('a +1/+1', 'that many +1/+1'), TEXT+' Then draw a card.',
                     TEXT.replace('this creature.', 'target creature.'), TEXT.replace('+1/+1', '-1/-1')):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(text).status)
        with patch('quorune.compiler.counter_prevention_templates.fixed_counter_prevention_handler', return_value=None):
            self.assertNotEqual('exact', self.compile().status)


class FixedCounterPreventionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = spree.SpreeModeCostRuntimeTests.session
    add = spree.SpreeModeCostRuntimeTests.add
    prepare = spree.SpreeModeCostRuntimeTests.prepare
    checkpoint = spree.SpreeModeCostRuntimeTests.checkpoint
    resolve = spree.SpreeModeCostRuntimeTests.resolve
    replay = spree.SpreeModeCostRuntimeTests.replay
    assert_closed = spree.SpreeModeCostRuntimeTests.assert_closed
    reject = spree.SpreeModeCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'fixed-counter-prevention.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/token-entry-boundary-cards.json',
            ROOT/'tests/fixtures/fixed-counter-prevention-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Counter prevention', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def entrant(self, session, name='Phantom Nomad', count=2, *, seat='B'):
        card = self.add(session.engine, name, seat=seat, ref='counter-prevention-body')
        card.counters['+1/+1'] = count
        self.assert_closed(session, card)
        return card

    def cast_shock(self, session, spell, target):
        action = 'cast:'+spell.ref
        self.reject(session, action, 'D', targets=[target.ref], pay='auto')
        accepted = session.act('pilot:A', {'action_id': action, 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))

    def test_actual_shocks_remove_one_counter_per_occurrence_and_replay(self):
        session = self.session(61512001)
        body = self.entrant(session, 'Phantom Tiger', 3)
        first = self.add(session.engine, 'Shock', zone='hand', ref='counter-shock-one')
        second = self.add(session.engine, 'Shock', zone='hand', ref='counter-shock-two')
        session.state.config.semantic_policy = 'trusted_only'
        self.prepare(session, mana={'R': 2})
        self.checkpoint(session)
        self.cast_shock(session, first, body)
        current = session.state.cards[body.object_id]
        self.assertEqual(2, current.counters['+1/+1'])
        self.assertEqual(0, current.marked_damage)
        session = self.replay(session, load=True)
        self.cast_shock(session, second, session.state.cards[body.object_id])
        self.assertEqual(1, session.state.cards[body.object_id].counters['+1/+1'])
        self.assertEqual(0, session.state.cards[body.object_id].marked_damage)
        self.replay(session, load=True)

    def test_simultaneous_sources_prevent_all_damage_and_remove_only_one_counter(self):
        session = self.session(61512002)
        body = self.entrant(session, 'Oathsworn Knight', 4)
        one = self.add(session.engine, 'Generic Bound Body', ref='sim-source-one')
        two = self.add(session.engine, 'Generic Bound Body', seat='C', ref='sim-source-two')
        proposals = tuple(damage_proposal(session.engine, source_ref=source.ref, target=body.ref,
            amount=amount, actor=source.controller, combat=True, proposal_id='fixed:'+source.ref, reason='fixed simultaneous test')
            for source, amount in ((one, 2), (two, 3)))
        result = commit_prepared_damage_batch(session.engine, prepare_damage_batch(session.engine, proposals))
        self.assertEqual(0, result.dealt_amount)
        self.assertEqual(3, body.counters['+1/+1'])
        self.assertEqual(0, body.marked_damage)

    def test_unpreventable_damage_is_dealt_and_still_removes_one_counter(self):
        session = self.session(61512003)
        body = self.entrant(session, 'Phantom Tiger', 4)
        source = self.add(session.engine, 'Generic Bound Body', ref='unpreventable-source')
        proposal = damage_proposal(session.engine, source_ref=source.ref, target=body.ref,
            amount=1, actor='A', unpreventable=True, combat=False, proposal_id='fixed:unpreventable', reason='fixed unpreventable test')
        result = commit_prepared_damage_batch(session.engine, prepare_damage_batch(session.engine, (proposal,)))
        self.assertEqual(1, result.dealt_amount)
        self.assertEqual(3, body.counters['+1/+1'])
        self.assertEqual(1, body.marked_damage)

    def test_conditional_source_at_zero_counters_does_not_prevent_damage(self):
        session = self.session(61512004)
        body = self.entrant(session, 'Undergrowth Champion', 0)
        source = self.add(session.engine, 'Generic Bound Body', ref='no-counter-source')
        proposal = damage_proposal(session.engine, source_ref=source.ref, target=body.ref, amount=1, actor='A',
            combat=False, proposal_id='fixed:zero-counter', reason='fixed zero-counter test')
        result = commit_prepared_damage_batch(session.engine, prepare_damage_batch(session.engine, (proposal,)))
        self.assertEqual(1, result.dealt_amount)
        self.assertEqual(0, body.counters.get('+1/+1', 0))

    def test_unconditional_zero_counter_source_with_actual_toughness_boost_still_prevents(self):
        session = self.session(61512005)
        anthem = self.add(session.engine, 'Glorious Anthem', ref='phantom-zero-anthem')
        self.assert_closed(session, anthem)
        body = self.entrant(session, 'Phantom Tiger', 0, seat='A')
        shock = self.add(session.engine, 'Shock', zone='hand', ref='phantom-zero-shock')
        session.state.config.semantic_policy = 'trusted_only'
        self.prepare(session, mana={'R': 1})
        self.checkpoint(session)
        self.cast_shock(session, shock, body)
        current = session.state.cards[body.object_id]
        self.assertEqual('battlefield', current.zone)
        self.assertEqual(0, current.counters.get('+1/+1', 0))
        self.assertEqual(0, current.marked_damage)
        self.assertEqual(1, session.engine._numeric_stat(current.object_id, 'toughness'))
        self.replay(session, load=True)

    def test_actual_multiple_blockers_share_one_counter_removal_and_precombat_replay(self):
        session = self.session(61512006)
        attacker = self.entrant(session, 'Oathsworn Knight', 4, seat='A')
        one = self.add(session.engine, 'Generic Bound Body', seat='B', ref='phantom-blocker-one')
        two = self.add(session.engine, 'Generic Bound Body', seat='B', ref='phantom-blocker-two')
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.started = True
        engine.state.active_player = 'A'
        engine.state.phase = 'combat'
        engine.state.step = 'declare_blockers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_blockers'))
        attacker.attacking = 'B'
        engine.state.combat = CombatState(attackers_declared=True,
            attackers={attacker.object_id: 'B'}, defending_players=['B'])
        engine._begin_blocker_decisions()
        self.checkpoint(session)
        accepted = session.act('pilot:B', {'action_id': 'block',
            'blocks': {one.ref: attacker.ref, two.ref: attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(20):
            if session.state.pending_decision.kind == 'combat.damage':
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('combat.damage', session.state.pending_decision.kind)
        accepted = session.act('pilot:A', {'action_id': 'assign_damage', 'assignments': [
            {'source': attacker.ref, 'target': one.ref, 'amount': 2},
            {'source': attacker.ref, 'target': two.ref, 'amount': 2}]})
        self.assertTrue(accepted.ok, accepted.summary)
        current = session.state.cards[attacker.object_id]
        self.assertEqual(3, current.counters['+1/+1'])
        self.assertEqual(0, current.marked_damage)
        self.assertEqual(2, session.state.cards[one.object_id].marked_damage)
        self.assertEqual(2, session.state.cards[two.object_id].marked_damage)
        self.replay(session, load=True)

    def test_protection_can_prevent_first_without_spending_a_counter(self):
        session = self.session(61512007)
        body = self.entrant(session, 'Phantom Centaur', 3)
        source = self.add(session.engine, 'Generic Bound Body', ref='black-damage-source')
        from quorune.damage import source_snapshot
        snapshot = replace(source_snapshot(session.engine, source.ref, controller='A'), colors=('B',))
        proposal = damage_proposal(session.engine, source_ref=source.ref, source_override=snapshot,
            target=body.ref, amount=2, actor='A', combat=False, proposal_id='fixed:protection', reason='protection control')
        with self.assertRaises(ReplacementChoiceRequired) as caught:
            prepare_damage_batch(session.engine, (proposal,))
        protection = next(effect.effect_id for effect in caught.exception.effects if 'protection' in effect.effect_id)
        result = commit_prepared_damage_batch(session.engine,
            prepare_damage_batch(session.engine, (proposal,), selections=(protection,)))
        self.assertEqual(0, result.dealt_amount)
        self.assertEqual(3, body.counters['+1/+1'])

    def test_unpreventable_damage_keeps_an_existing_finite_shield_unchanged(self):
        session = self.session(61512008)
        body = self.entrant(session, 'Phantom Tiger', 4)
        source = self.add(session.engine, 'Generic Bound Body', ref='shield-damage-source')
        shield = DamagePreventionShield(shield_id='fixed-finite', source_id='fixed-finite-source', controller='B',
            subject=DamageSubject(ref=body.ref, kind='permanent', controller='B',
                object_id=body.object_id, logical_object_id=body.logical_object_id, owner=body.owner),
            mode=PreventionMode.AMOUNT, remaining=3, duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=session.state.turn_sequence)
        session.state.damage_prevention_shields.append(shield)
        proposal = damage_proposal(session.engine, source_ref=source.ref, target=body.ref, amount=1, actor='A',
            combat=False, unpreventable=True, proposal_id='fixed:finite-unpreventable', reason='finite shield control')
        selections = []
        for _ in range(4):
            try:
                prepared = prepare_damage_batch(session.engine, (proposal,), selections=selections)
                break
            except ReplacementChoiceRequired as required:
                selections.append(required.pending.choice.options[0])
        else:
            self.fail('Unpreventable prevention ordering did not converge')
        result = commit_prepared_damage_batch(session.engine, prepared)
        self.assertEqual(1, result.dealt_amount)
        self.assertEqual(3, body.counters['+1/+1'])
        self.assertEqual(3, session.state.damage_prevention_shields[0].remaining)

    def test_nested_prevention_damage_observes_the_counter_already_removed(self):
        self._nested_counter_timing(count=1, expected=[1, 1])

    def test_sequential_nested_occurrences_update_the_conditional_counter_predicate(self):
        self._nested_counter_timing(count=2, expected=[0, 1])

    def test_stale_nested_counter_plan_rejects_before_companion_player_damage(self):
        self._nested_counter_timing(count=2, expected=[0, 1], stale=True)

    def test_missing_nested_counter_result_rejects_before_companion_player_damage(self):
        self._nested_counter_timing(count=2, expected=[0, 1], stale='missing')

    def _nested_counter_timing(self, *, count, expected, stale=False):
        session = self.session(61512009)
        body = self.entrant(session, 'Undergrowth Champion', count)
        source = self.add(session.engine, 'Generic Bound Body', ref='nested-counter-source')
        from quorune.damage import source_snapshot
        subject = DamageSubject(ref=body.ref, kind='permanent', controller=body.controller,
            object_id=body.object_id, logical_object_id=body.logical_object_id, owner=body.owner)
        session.state.damage_prevention_shields.append(DamagePreventionShield(
            shield_id='nested-counter-shield', source_id=source.ref, controller='A',
            subject=DamageSubject(ref='C', kind='player', controller='C'),
            mode=PreventionMode.NEXT_INSTANCE, remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=session.state.turn_sequence,
            aftermath=tuple(DealDamagePreventionAftermath(source=source_snapshot(session.engine, source.ref, controller='A'),
                recipient=DamageAftermathRecipient(kind='fixed', subject=subject), fixed_amount=1) for _ in range(2))))
        proposals = tuple(damage_proposal(session.engine, source_ref=source.ref, target=target, amount=1,
            actor='A', combat=False, proposal_id='nested:'+target, reason='nested prevention timing')
            for target in (body.ref, 'C', 'D'))
        sources = session.engine._semantic_event_sources()
        from quorune.record import authoritative_state_hash
        before_prepare = authoritative_state_hash(session.state)
        prepared = prepare_damage_batch(session.engine, proposals,
            sources=sources, source_zones={card.object_id: card.zone for card in sources})
        self.assertEqual(before_prepare, authoritative_state_hash(session.state))
        if stale:
            from quorune.record import authoritative_state_hash
            instruction = next(v for v in prepared.aftermath.instructions if v.nested_damage is not None)
            removal = instruction.nested_damage.aftermath.counter_removals[0]
            transition, = removal.plan.counter_plan.transitions
            corrupted = replace(transition, before=99, after=98)
            broken_plan = replace(removal.plan, counter_plan=replace(removal.plan.counter_plan,
                transitions=(corrupted,)))
            broken_removal = replace(removal, plan=broken_plan)
            broken_nested = replace(instruction.nested_damage, aftermath=replace(
                instruction.nested_damage.aftermath,
                counter_removals=() if stale == 'missing' else (broken_removal,)))
            broken_instruction = replace(instruction, nested_damage=broken_nested)
            prepared = replace(prepared, aftermath=replace(prepared.aftermath,
                instructions=tuple(broken_instruction if v is instruction else v for v in prepared.aftermath.instructions)))
            before = authoritative_state_hash(session.state)
            with self.assertRaises(ValueError):
                commit_prepared_damage_batch(session.engine, prepared)
            self.assertEqual(before, authoritative_state_hash(session.state))
            return
        result = commit_prepared_damage_batch(session.engine, prepared)
        self.assertEqual(0, body.counters.get('+1/+1', 0))
        self.assertEqual(sum(expected), body.marked_damage)
        self.assertEqual(expected, [nested.dealt_amount for nested in result.nested_damage_results])

    def test_actual_pending_choice_is_controller_private_atomic_loaded_and_replayed(self):
        session = self.session(61512010)
        body = self.entrant(session, 'Phantom Centaur', 3)
        source = self.add(session.engine, 'Generic Bound Body', ref='pending-black-attacker')
        hidden = self.add(session.engine, 'Generic Bound Growth', seat='D', zone='hand', ref='prevention-private-d')
        commit_continuous_effect(session.state, ContinuousEffect(effect_id='fixed:black-stack-source',
            source_id='fixed:black-stack-source', layer=Layer.COLOR, sublayer='5',
            timestamp=session.engine._next_zone_timestamp(), operations=(ContinuousOperation('set_colors', ('B',)),),
            origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(ContinuousObjectIdentity(source.object_id, source.logical_object_id),)))
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.started = True
        engine.state.active_player = 'A'
        engine.state.phase = 'combat'
        engine.state.step = 'declare_blockers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_blockers'))
        source.attacking = 'B'
        engine.state.combat = CombatState(attackers_declared=True,
            attackers={source.object_id: 'B'}, defending_players=['B'])
        engine._begin_blocker_decisions()
        self.checkpoint(session)
        accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {body.ref: source.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(16):
            if session.state.pending_decision.kind == 'replacement.order':
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('replacement.order', session.state.pending_decision.kind)
        self.assertEqual(['pilot:B'], session.pending_principals())
        decision = session.packet('pilot:B', full=True)['decision']
        selected = next(r['id'] for r in decision['ctx']['options'] if 'fixed-self-counter' in r['id'])
        self.reject(session, 'choose', 'A', choices={'replacement': selected})
        self.reject(session, 'choose', 'B', choices={'replacement': 'unknown'})
        for seat in 'ACD':
            self.assertIsNone(session.packet('pilot:'+seat, full=True)['decision'])
        session = self.replay(session, load=True)
        result = session.act('pilot:B', {'action_id': 'choose', 'choices': {'replacement': selected}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[body.object_id]
        self.assertEqual(2, current.counters['+1/+1'])
        self.assertEqual(0, current.marked_damage)
        for seat in 'ABC':
            self.assertNotIn(hidden.ref, json.dumps(session.packet('pilot:'+seat, full=True)))
        self.replay(session, load=True)

    def test_stale_prepared_counter_result_rejects_before_damage_or_life_mutation(self):
        from quorune.record import authoritative_state_hash
        session = self.session(61512011)
        body = self.entrant(session, 'Phantom Tiger', 3)
        source = self.add(session.engine, 'Generic Bound Body', ref='stale-counter-damage-source')
        proposal = damage_proposal(session.engine, source_ref=source.ref, target=body.ref, amount=1,
            actor='A', combat=False, proposal_id='fixed:stale', reason='stale removal plan', unpreventable=True)
        prepared = prepare_damage_batch(session.engine, (proposal,))
        body.counters['+1/+1'] = 2
        before = authoritative_state_hash(session.state)
        with self.assertRaises(ValueError):
            commit_prepared_damage_batch(session.engine, prepared)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_current_ability_removal_allows_damage_without_spending_a_counter(self):
        session = self.session(61512012)
        body = self.entrant(session, 'Phantom Tiger', 3)
        self.add(session.engine, 'Humility', ref='phantom-humility')
        spell = self.add(session.engine, 'Shock', zone='hand', ref='phantom-humility-shock')
        self.prepare(session, mana={'R': 1})
        self.checkpoint(session)
        self.cast_shock(session, spell, body)
        current = session.state.cards[body.object_id]
        self.assertEqual(3, current.counters['+1/+1'])
        self.assertEqual(2, current.marked_damage)
        self.replay(session, load=True)

    def test_copied_original_uses_its_own_counters_and_current_prevention(self):
        session = self.session(61512013)
        original = self.entrant(session, 'Phantom Tiger', 5, seat='A')
        spell = self.add(session.engine, 'Generic Entry One Copy', zone='hand', ref='phantom-copy')
        shock = self.add(session.engine, 'Shock', zone='hand', ref='phantom-copy-shock')
        self.prepare(session, mana={'W': 1, 'R': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': 'cast:'+spell.ref, 'targets': [original.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(2, token.counters['+1/+1'])
        self.cast_shock(session, shock, token)
        self.assertEqual(1, session.state.cards[token.object_id].counters['+1/+1'])
        self.assertEqual(5, session.state.cards[original.object_id].counters['+1/+1'])
        self.assertEqual(0, session.state.cards[token.object_id].marked_damage)
        self.replay(session, load=True)

    def test_omitting_counter_removal_is_killed_by_the_native_consequence(self):
        from quorune.counter_removal import CounterRemovalResult
        def omit(host, plan):
            before = host.state.cards[plan.removal.object_id].counters.get(plan.removal.counter_name, 0)
            return CounterRemovalResult(plan.removal.object_id, plan.removal.counter_name,
                plan.removal.amount, 0, before, before)
        with patch('quorune.counter_prevention.commit_counter_removal_effect', side_effect=omit), self.assertRaises(AssertionError):
            self.test_actual_shocks_remove_one_counter_per_occurrence_and_replay()

    def test_manifested_original_has_no_front_prevention_or_counter_removal(self):
        session = self.session(61512014)
        body = self.add(session.engine, 'Phantom Tiger', zone='library', ref='manifested-phantom')
        manifest = self.add(session.engine, 'Soul Summons', zone='hand', ref='phantom-manifest')
        shock = self.add(session.engine, 'Shock', zone='hand', ref='phantom-manifest-shock')
        library = session.state.players['A'].zones['library']
        library.remove(body.object_id)
        library.append(body.object_id)
        self.prepare(session, mana={'W': 1, 'R': 2})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': 'cast:'+manifest.ref, 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[body.object_id].face_down)
        self.assertEqual({}, session.state.cards[body.object_id].counters)
        self.cast_shock(session, shock, session.state.cards[body.object_id])
        self.assertEqual('graveyard', session.state.cards[body.object_id].zone)
        self.replay(session, load=True)

    def test_first_and_regular_strike_are_distinct_counter_removal_occurrences(self):
        session = self.session(61512015)
        body = self.entrant(session, 'Phantom Tiger', 3, seat='B')
        attacker = self.add(session.engine, 'Generic Bound Body', ref='double-strike-phantom-attacker')
        commit_continuous_effect(session.state, ContinuousEffect(effect_id='fixed:double-strike-attacker',
            source_id='fixed:double-strike-attacker', layer=Layer.ABILITY, sublayer='6',
            timestamp=session.engine._next_zone_timestamp(), operations=(ContinuousOperation('add_ability', 'Double Strike'),),
            origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(ContinuousObjectIdentity(attacker.object_id, attacker.logical_object_id),)))
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.started = True
        engine.state.active_player = 'A'
        engine.state.phase = 'combat'
        engine.state.step = 'declare_blockers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_blockers'))
        attacker.attacking = 'B'
        engine.state.combat = CombatState(attackers_declared=True,
            attackers={attacker.object_id: 'B'}, defending_players=['B'])
        engine._begin_blocker_decisions()
        self.checkpoint(session)
        accepted = session.act('pilot:B', {'action_id': 'block', 'blocks': {body.ref: attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(24):
            if session.state.cards[body.object_id].counters.get('+1/+1') == 1:
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, session.state.cards[body.object_id].counters['+1/+1'])
        self.assertEqual(0, session.state.cards[body.object_id].marked_damage)
        self.replay(session, load=True)

    def test_additional_counter_placement_observes_the_prevention_counter_removal(self):
        session = self.session(61512016)
        body = self.entrant(session, 'Undergrowth Champion', 2)
        source = self.add(session.engine, 'Generic Bound Body', ref='counter-aftermath-source')
        subject = DamageSubject(ref=body.ref, kind='permanent', controller=body.controller,
            object_id=body.object_id, logical_object_id=body.logical_object_id, owner=body.owner)
        session.state.damage_prevention_shields.append(DamagePreventionShield(
            shield_id='counter-placement-aftermath', source_id=source.ref, controller='A',
            subject=DamageSubject(ref='C', kind='player', controller='C'),
            mode=PreventionMode.NEXT_INSTANCE, remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=session.state.turn_sequence,
            aftermath=(PlaceCountersPreventionAftermath(subject=subject, counter_name='+1/+1',
                placing_player='B', fixed_amount=1),)))
        proposals = tuple(damage_proposal(session.engine, source_ref=source.ref, target=target, amount=1,
            actor='A', combat=False, proposal_id='placement-after:'+target, reason='counter result timing')
            for target in (body.ref, 'C'))
        result = commit_prepared_damage_batch(session.engine, prepare_damage_batch(session.engine, proposals))
        self.assertEqual(2, body.counters['+1/+1'])
        self.assertEqual(0, body.marked_damage)
        self.assertEqual('place_counters', result.aftermath_events[0].kind)

    def test_missing_prepared_counter_result_cannot_commit_companion_player_damage(self):
        from quorune.record import authoritative_state_hash
        session = self.session(61512017)
        body = self.entrant(session, 'Phantom Tiger', 3)
        source = self.add(session.engine, 'Generic Bound Body', ref='missing-removal-source')
        proposals = tuple(damage_proposal(session.engine, source_ref=source.ref, target=target, amount=1,
            actor='A', combat=False, proposal_id='missing:'+target, reason='missing result control')
            for target in (body.ref, 'C'))
        prepared = prepare_damage_batch(session.engine, proposals)
        broken = replace(prepared, aftermath=replace(prepared.aftermath, counter_removals=()))
        before = authoritative_state_hash(session.state)
        with self.assertRaises(ValueError):
            commit_prepared_damage_batch(session.engine, broken)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_unpreventable_infect_and_additional_counter_removal_share_the_damage_boundary(self):
        session = self.session(61512018)
        body = self.entrant(session, 'Phantom Tiger', 4)
        source = self.add(session.engine, 'Generic Bound Body', ref='unpreventable-infect-source')
        from quorune.damage import source_snapshot
        snapshot = replace(source_snapshot(session.engine, source.ref, controller='A'), keywords=('infect',))
        proposal = damage_proposal(session.engine, source_ref=source.ref, source_override=snapshot,
            target=body.ref, amount=1, actor='A', combat=False, unpreventable=True,
            proposal_id='fixed:unpreventable-infect', reason='infect/removal interaction')
        result = commit_prepared_damage_batch(session.engine, prepare_damage_batch(session.engine, (proposal,)))
        self.assertEqual(1, result.dealt_amount)
        self.assertEqual(3, body.counters['+1/+1'])
        self.assertEqual(1, body.counters['-1/-1'])
        self.assertEqual(0, body.marked_damage)

    def test_additional_removal_precedes_companion_damage_result_mutation(self):
        session = self.session(61512019)
        body = self.entrant(session, 'Phantom Tiger', 3)
        source = self.add(session.engine, 'Generic Bound Body', ref='timing-companion-source')
        proposals = tuple(damage_proposal(session.engine, source_ref=source.ref, target=target, amount=1,
            actor='A', combat=False, proposal_id='timing:'+target, reason='immediate additional removal')
            for target in (body.ref, 'C'))
        from quorune.counter_removal import commit_counter_removal_effect
        observed = []
        def observe(host, plan):
            observed.append(host.state.players['C'].life)
            return commit_counter_removal_effect(host, plan)
        with patch('quorune.counter_prevention.commit_counter_removal_effect', side_effect=observe):
            commit_prepared_damage_batch(session.engine, prepare_damage_batch(session.engine, proposals))
        self.assertEqual([40], observed)
        self.assertEqual(39, session.state.players['C'].life)


if __name__ == '__main__':
    unittest.main()
