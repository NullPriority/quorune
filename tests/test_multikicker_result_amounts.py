from __future__ import annotations

"""CR 400.7d/702.33e/707.10: results consume the linked paid count."""

from copy import deepcopy
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
from quorune.multikicker_model import FixedManaMultikickerSpec, MULTIKICKER_FIELD
from quorune.multikicker_result_amounts import MULTIKICKER_AMOUNT_CAPABILITY, resolve_multikicker_amount
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.query_effect_amount_model import PublicQueryAmountError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls

NAMES = ('Spell Contortion', 'Deathforge Shaman', 'Wolfbriar Elemental',
         'Lightkeeper of Emeria')


class MultikickerResultValueTests(unittest.TestCase):
    def test_entry_metadata_rejects_foreign_transitions_and_preserves_legacy_unread_facts(self):
        from quorune.model import CardInstance
        from quorune.zone_object_state import stack_entry_cast_facts, mark_multikicker_paid
        from test_zone_trigger_events import occurrence
        from quorune.zone_trigger_events import normalized_zone_trigger_events
        cost = FixedManaMultikickerSpec('front:multikicker:line:1',
            {'GENERIC': 0, 'W': 1, 'U': 0, 'B': 0, 'R': 0, 'G': 0, 'C': 0})
        card = CardInstance(object_id='linked', ref='linked-ref', oracle_id='generic:linked',
            printed_name='Linked', owner='A', controller='A', zone='stack')
        mark_multikicker_paid(card, {MULTIKICKER_FIELD: cost.payment_fact(2)})
        self.assertEqual({'cast_option': 'kicked', 'paid_entry_copy': None}, stack_entry_cast_facts(card))
        facts = stack_entry_cast_facts(card, include_multikicker_result=True)
        self.assertEqual(cost.payment_fact(2), facts[MULTIKICKER_FIELD])
        changed = occurrence(origin='stack', destination='battlefield', cast_option='kicked',
            multikicker_paid=cost.payment_fact(2))
        self.assertEqual(cost.payment_fact(2), changed.to_dict()[MULTIKICKER_FIELD])
        event = next(event for event in normalized_zone_trigger_events(changed) if event.kind == 'permanent.enter')
        self.assertEqual(cost.payment_fact(2), dict(event.context[MULTIKICKER_FIELD]))
        for values in ({'origin': 'graveyard'}, {'destination': 'hand'}, {'cast_option': None},
                       {'multikicker_paid': {**cost.payment_fact(2), 'count': True}}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                occurrence(origin=values.get('origin', 'stack'), destination=values.get('destination', 'battlefield'),
                    cast_option=values.get('cast_option', 'kicked'),
                    multikicker_paid=values.get('multikicker_paid', cost.payment_fact(2)))

    def test_linked_count_rejects_malformed_and_mismatched_payment_facts(self):
        cost = FixedManaMultikickerSpec('front:multikicker:line:1',
            {'GENERIC': 1, 'W': 0, 'U': 1, 'B': 0, 'R': 0, 'G': 0, 'C': 0})
        spec = ScalarEffectAmountSpec(ScalarAmountOrigin.MULTIKICKER_COUNT,
            schema_version=5, multikicker=cost)
        self.assertEqual(spec, ScalarEffectAmountSpec.from_dict(spec.to_dict()))
        self.assertEqual(0, resolve_multikicker_amount(spec, SimpleNamespace(context={})))
        self.assertEqual(3, resolve_multikicker_amount(spec,
            SimpleNamespace(context={MULTIKICKER_FIELD: cost.payment_fact(3)})))
        for fact in ({}, {**cost.payment_fact(2), 'count': True},
                     {**cost.payment_fact(2), 'instance_id': 'another'},
                     {**cost.payment_fact(2), 'cost_fingerprint': 'different'}):
            with self.subTest(fact=fact), self.assertRaises(ValueError):
                resolve_multikicker_amount(spec, SimpleNamespace(context={MULTIKICKER_FIELD: fact}))
        for changed in ({'schema_version': 4}, {'multikicker': None}, {'characteristic': 'power'},
                        {'history_fact': 'cards_drawn'}, {'unknown': 1}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                ScalarEffectAmountSpec.from_dict({**spec.to_dict(), **changed})


class MultikickerResultRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.CompoundDamageRuntimeTests.session
    add = controls.CompoundDamageRuntimeTests.add
    prepare = controls.CompoundDamageRuntimeTests.prepare
    checkpoint = controls.CompoundDamageRuntimeTests.checkpoint
    cast = controls.CompoundDamageRuntimeTests.cast
    resolve = controls.CompoundDamageRuntimeTests.resolve
    replay = controls.CompoundDamageRuntimeTests.replay
    binding = controls.CompoundDamageRuntimeTests.binding
    actions = controls.CompoundDamageRuntimeTests.actions
    action = controls.CompoundDamageRuntimeTests.action
    private_replay = controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'multikicker-results.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/multikicker-result-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Linked Multikicker results',
            [DeckEntry('Generic Bound Commander', 1, 'commander'),
             DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def cast_multikicker(self, session, source, count, *, targets=()):
        source = session.state.cards[source.object_id]
        action = self.action(session, source)
        command = {'action_id': action['id'], 'pay': 'auto', 'targets': list(targets)}
        if count:
            command['cost_option'] = next(row['id'] for row in action['cost_options'] if '+multikicker:' in row['id'])
            command['multikicker_count'] = count
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def resolve_object(self, session, object_id):
        for _ in range(48):
            if not any(item.card_object_id == object_id for item in session.state.stack):
                return
            decision = session.state.pending_decision
            self.assertEqual('priority', decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('The selected stack object did not resolve')

    def test_four_originals_bind_results_to_the_printed_cost_and_remain_fully_admitted(self):
        from quorune.scalar_effect_amount_model import scalar_amount_specs
        for index, name in enumerate(NAMES):
            session = self.session(731010 + index)
            source = self.add(session.engine, name, zone='hand')
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            count_node = next(node for node in ir.faces[0].nodes if node.template_id == 'linked-multikicker-count-result-v1')
            cost_node = next(node for node in ir.faces[0].nodes if node.template_id == 'fixed-mana-multikicker-v1')
            for amount in scalar_amount_specs(count_node.effects):
                self.assertEqual(cost_node.handlers[0]['multikicker'], amount.multikicker.to_dict())
            self.assertEqual(count_node.text, card.oracle_text[count_node.span.start:count_node.span.end])
            programs = session.engine.semantics.programs_for_oracle(card.oracle_id)
            self.assertTrue(any(MULTIKICKER_AMOUNT_CAPABILITY in program.capability_dependencies
                                and program.effects for program in programs), name)
            binding = self.binding(session, source)
            self.assertTrue(binding['strict_capability_ready'], (name, binding))
            preflight = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', preflight['status'], name)

    def test_actual_lightkeeper_pays_zero_or_three_and_uses_sealed_entry_count_after_source_departure(self):
        for count in (0, 3):
            session = self.session(731030 + count)
            source = self.add(session.engine, 'Lightkeeper of Emeria', zone='hand')
            bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            session.state.players['B'].mana_pool['U'] = 1
            self.prepare(session, mana={'C': 6, 'W': 5})
            self.checkpoint(session)
            self.cast_multikicker(session, source, count)
            self.assertEqual(7 - count, sum(session.state.players['A'].mana_pool.values()))
            self.resolve_object(session, source.object_id)
            self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
            self.assertTrue(session.state.stack, 'The entry result must be a separate trigger')
            self.assertEqual(40, session.state.players['A'].life)
            self.cast(session, 'B', bounce, [source.ref])
            self.resolve_object(session, bounce.object_id)
            self.assertEqual('hand', session.state.cards[source.object_id].zone)
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(40 + 2 * count, session.state.players['A'].life)
            previous = session.state.cards[source.object_id].logical_object_id
            self.cast_multikicker(session, source, 0)
            self.assertIsNone(self.resolve(session))
            self.assertNotEqual(previous, session.state.cards[source.object_id].logical_object_id)
            self.assertEqual(40 + 2 * count, session.state.players['A'].life)
            self.private_replay(session, private)

    def test_actual_wolfbriar_zero_or_two_payments_create_exact_wolves_after_source_departure(self):
        for count in (0, 2):
            session = self.session(731050 + count)
            source = self.add(session.engine, 'Wolfbriar Elemental', zone='hand')
            bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            session.state.players['B'].mana_pool['U'] = 1
            self.prepare(session, mana={'C': 2, 'G': 4})
            self.checkpoint(session)
            self.cast_multikicker(session, source, count)
            self.resolve_object(session, source.object_id)
            self.cast(session, 'B', bounce, [session.state.cards[source.object_id].ref])
            self.assertIsNone(self.resolve(session))
            self.assertEqual('hand', session.state.cards[source.object_id].zone)
            wolves = [card for card in session.state.cards.values() if card.zone == 'battlefield' and card.is_token]
            self.assertEqual(count, len(wolves))
            for wolf in wolves:
                self.assertEqual('A', wolf.controller)
                data = session.engine._effective_card_data(wolf)
                self.assertEqual(['G'], list(data['colors']))
                self.assertIn('Wolf', data['type_line'])
                self.assertEqual((2, 2), tuple(session.engine._numeric_stat(wolf.object_id, field) for field in ('power', 'toughness')))
            self.private_replay(session, private)

    def test_actual_deathforge_targets_a_player_and_keeps_twice_the_paid_count_after_departure(self):
        for count in (0, 2):
            session = self.session(731060 + count)
            source = self.add(session.engine, 'Deathforge Shaman', zone='hand')
            bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            session.state.players['B'].mana_pool['U'] = 1
            self.prepare(session, mana={'C': 4, 'R': 3})
            self.checkpoint(session)
            self.cast_multikicker(session, source, count)
            pending = self.resolve(session)
            self.assertEqual('semantic.target', pending.kind)
            session = self.replay(session, load=True)
            before = authoritative_state_hash(session.state)
            command = {'action_id': 'choose', 'targets': ['D']}
            self.assertFalse(session.act('pilot:B', command).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            self.assertTrue(session.act('pilot:A', command).ok)
            self.cast(session, 'B', bounce, [session.state.cards[source.object_id].ref])
            self.assertIsNone(self.resolve(session))
            self.assertEqual([40, 40, 40, 40 - 2 * count], [session.state.players[seat].life for seat in 'ABCD'])
            self.assertEqual('hand', session.state.cards[source.object_id].zone)
            self.private_replay(session, private)

    def test_actual_contortion_copy_retains_payments_under_new_controller_and_original_fizzles(self):
        session = self.session(731070)
        source = self.add(session.engine, 'Spell Contortion', zone='hand')
        copy = self.add(session.engine, 'Reverberate', seat='B', zone='hand')
        victim = self.add(session.engine, 'Generic Bound Growth', seat='D', zone='hand', ref='contortion-victim')
        body = self.add(session.engine, 'Generic Bound Body', seat='D')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='contortion-private')
        session.state.players['B'].mana_pool['R'] = 2
        session.state.players['D'].mana_pool['G'] = 1
        self.prepare(session, mana={'C': 4, 'U': 3})
        self.checkpoint(session)
        self.cast(session, 'D', victim, [body.ref])
        target = next(item for item in session.state.stack if item.card_object_id == victim.object_id)
        # Return priority through actual passes to cast the original counter.
        for _ in range(4):
            if session.state.priority_player == 'A':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.cast_multikicker(session, source, 2, targets=[target.ref])
        original = next(item for item in session.state.stack if item.card_object_id == source.object_id)
        self.cast(session, 'B', copy, [original.ref])
        pending = self.resolve(session)
        self.assertEqual('semantic.choice', pending.kind)
        session = self.replay(session, load=True)
        self.assertTrue(session.act('pilot:B', {'action_id': 'choose', 'targets': [target.ref]}).ok)
        hand_a = len(session.state.players['A'].zones['hand'])
        hand_b = len(session.state.players['B'].zones['hand'])
        pending = self.resolve(session)
        if pending is not None:
            self.assertEqual(['pilot:D'], session.pending_principals())
            self.assertTrue(session.act('pilot:D', {'action_id': 'choose', 'pay': False}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand_b + 2, len(session.state.players['B'].zones['hand']))
        self.assertEqual(hand_a, len(session.state.players['A'].zones['hand']))
        for viewer in 'ACD':
            packet = json.dumps(session.packet('pilot:' + viewer, full=True))
            for object_id in session.state.players['B'].zones['hand']:
                self.assertNotIn(session.state.cards[object_id].ref, packet)
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.assertEqual(3, len(session.engine._current_turn_history('spell_cast')))
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session, private)

    def test_actual_contortion_zero_or_two_payments_draws_after_the_target_controller_pays(self):
        for count in (0, 2):
            session = self.session(731090 + count)
            source = self.add(session.engine, 'Spell Contortion', zone='hand')
            victim = self.add(session.engine, 'Generic Bound Growth', seat='D', zone='hand', ref='paid-contortion-victim')
            body = self.add(session.engine, 'Generic Bound Body', seat='D')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='paid-contortion-private')
            session.state.players['D'].mana_pool.update({'C': 2, 'G': 1})
            self.prepare(session, mana={'C': 4, 'U': 3})
            self.checkpoint(session)
            self.cast(session, 'D', victim, [body.ref])
            target = next(item for item in session.state.stack if item.card_object_id == victim.object_id)
            while session.state.priority_player != 'A':
                self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
            self.cast_multikicker(session, source, count, targets=[target.ref])
            self.assertEqual(4 - 2 * count, sum(session.state.players['A'].mana_pool.values()))
            before_hand = len(session.state.players['A'].zones['hand'])
            pending = self.resolve(session)
            self.assertIsNotNone(pending)
            self.assertEqual(['pilot:D'], session.pending_principals())
            session = self.replay(session, load=True)
            before = authoritative_state_hash(session.state)
            command = {'action_id': 'choose', 'pay': True}
            self.assertFalse(session.act('pilot:A', command).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
            self.assertTrue(session.act('pilot:D', command).ok)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(before_hand + count, len(session.state.players['A'].zones['hand']))
            self.assertEqual(0, sum(session.state.players['D'].mana_pool.values()))
            self.assertEqual(4, session.engine._numeric_stat(body.object_id, 'power'))
            self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
            self.private_replay(session, private)

    def test_actual_stifle_counters_the_entry_result_without_refunding_multikicker(self):
        session = self.session(731100)
        source = self.add(session.engine, 'Lightkeeper of Emeria', zone='hand')
        counter = self.add(session.engine, 'Stifle', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['U'] = 1
        self.prepare(session, mana={'C': 3, 'W': 4})
        self.checkpoint(session)
        self.cast_multikicker(session, source, 3)
        self.resolve_object(session, source.object_id)
        trigger, = session.state.stack
        self.cast(session, 'B', counter, [trigger.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual([40] * 4, [session.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_unrepresented_count_discards_and_independent_siblings_remain_residual(self):
        card = self.db.lookup('Bloodhusk Ritualist')
        self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=self.registry,
            capability_profile='commander_review').status)
        card = self.db.lookup('Lightkeeper of Emeria')
        for changed in (replace(card, oracle_text=card.oracle_text + '\nWhenever a player sneezes, draw a card.'),
                        replace(card, oracle_text=card.oracle_text.replace('Multikicker {W}', 'Multikicker {W/U}')),
                        replace(card, oracle_text=card.oracle_text.replace('it was kicked', 'another spell was kicked'))):
            self.assertNotEqual('exact', compile_oracle_card(changed, capability_registry=self.registry,
                capability_profile='commander_review').status)

    def test_runtime_withdrawal_cannot_omit_the_count_or_cast_payment_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(731080)
        source = self.add(session.engine, 'Lightkeeper of Emeria', zone='hand')
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if program.event == 'permanent.enter.self')
        for omitted in (MULTIKICKER_AMOUNT_CAPABILITY, 'casting.multikicker.fixed_mana'):
            dependencies = tuple(cap for cap in program.capability_dependencies if cap != omitted)
            changed = replace(program, capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
            binding = bind_semantic_program_runtime(changed,
                capability_registry=self.registry, profile='commander_review')
            self.assertFalse(binding['strict'])
            self.assertIn('capability:undeclared_runtime_dependency:' + omitted, binding['blockers'])

    def test_fresh_lightkeeper_witness_kills_omitted_linked_paid_count(self):
        method = 'test_actual_lightkeeper_pays_zero_or_three_and_uses_sealed_entry_count_after_source_departure'
        with patch('quorune.multikicker_result_amounts.resolve_multikicker_amount', return_value=0):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_original_admission_kills_omitted_count_result_compiler(self):
        method = 'test_four_originals_bind_results_to_the_printed_cost_and_remain_fully_admitted'
        with patch('quorune.compiler.multikicker_results.multikicker_result_node', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
