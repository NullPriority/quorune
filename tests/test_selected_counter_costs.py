from __future__ import annotations

"""CR118.3/602.2b: a selected controlled creature pays one +1/+1 counter."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import CostChoice
from quorune.carddb import CardDatabase
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.selected_counter_costs import SELECTED_PLUS_COUNTER_COST_KIND, SELECTED_COUNTER_COST_CAPABILITY
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls

NAMES = ('Ray Fillet, Man Ray', 'Shapers of Nature', 'Spike Rogue', 'Bolrac-Clan Crusher',
         'Sunstar Chaplain', 'Ghave, Guru of Spores', 'Korozda Gorgon')


class SelectedCounterCostValueTests(unittest.TestCase):
    def test_fixed_choice_roundtrip_and_malformed_payment_shapes_reject(self):
        choice = CostChoice(SELECTED_PLUS_COUNTER_COST_KIND, card_type='creature')
        self.assertEqual(choice, CostChoice.from_dict(choice.to_dict()))
        self.assertEqual({'k': SELECTED_PLUS_COUNTER_COST_KIND, 'n': 1, 'z': 'battlefield', 't': 'creature'}, choice.compact())
        for changed in ({'count': 2}, {'count': True}, {'zone': 'hand'}, {'card_type': 'artifact'},
                        {'another': True}, {'predicate': {}}, {'unknown': True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                CostChoice.from_dict({**choice.to_dict(), **changed})


class SelectedCounterCostRuntimeTests(unittest.TestCase):
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
    private_replay = controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'selected-counters.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/controlled-counter-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Selected counter cost witnesses',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def selected_action(self, session, source):
        source = session.state.cards[source.object_id]
        return next(row for row in self.actions(session) if row['id'].startswith('activate:' + source.ref + ':')
            and any(choice.get('k') == SELECTED_PLUS_COUNTER_COST_KIND for choice in row['cost_summary'].get('choose_cost', ())))

    def test_seven_originals_have_whole_admission_and_authoritative_payment_forms(self):
        from quorune.choice_forms import build_action_form
        for index, name in enumerate(NAMES):
            session = self.session(734000 + index)
            source = self.add(session.engine, name)
            source.counters['+1/+1'] = 2
            self.prepare(session, mana={'C': 10, 'U': 3, 'G': 3})
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, (name, ir.material_residuals))
            self.assertTrue(self.binding(session, source)['strict_capability_ready'], name)
            status = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', status['status'], (name, status['unresolved']))
            abilities = compile_activated_ability_catalog(card)['front']
            self.assertTrue(any(any(choice.kind == SELECTED_PLUS_COUNTER_COST_KIND for choice in ability.choices) for ability in abilities))
            action = self.selected_action(session, source)
            self.assertEqual([source.ref], action['choice_schema']['cost_cards']['legal_refs'])
            form = build_action_form(action, decision_kind='priority',
                context=session.packet('pilot:A', full=True)['decision']['ctx'])
            self.assertIsNotNone(form)
            self.assertTrue(any(field['name'] == 'cost_cards' for field in form['fields']))

    def test_actual_shapers_uses_tapped_controlled_payer_and_rolls_back_bad_or_unfunded_selections(self):
        session = self.session(734020)
        source = self.add(session.engine, 'Shapers of Nature')
        payer = self.add(session.engine, 'Generic Bound Body', ref='selected-counter-payer')
        payer.counters['+1/+1'] = 1
        payer.tapped = True
        foreign = self.add(session.engine, 'Generic Bound Body', seat='B', ref='selected-counter-foreign')
        foreign.counters['+1/+1'] = 3
        empty = self.add(session.engine, 'Generic Bound Body', ref='selected-counter-empty')
        wrong_kind = self.add(session.engine, 'Hardened Scales')
        wrong_kind.counters['+1/+1'] = 1
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.prepare(session, mana={'C': 2, 'U': 1})
        self.checkpoint(session)
        action = self.selected_action(session, source)
        self.assertEqual([payer.ref], action['choice_schema']['cost_cards']['legal_refs'])
        before = authoritative_state_hash(session.state)
        good = {'action_id': action['id'], 'cost_cards': [payer.ref], 'pay': 'auto'}
        for actor, command in (
            ('pilot:B', good), ('pilot:A', {**good, 'cost_cards': []}),
            ('pilot:A', {**good, 'cost_cards': [payer.ref, payer.ref]}),
            ('pilot:A', {**good, 'cost_cards': [foreign.ref]}),
            ('pilot:A', {**good, 'cost_cards': [empty.ref]}),
            ('pilot:A', {**good, 'cost_cards': [wrong_kind.ref]}),
            ('pilot:A', {**good, 'pay': 'manual', 'payment': {'C': 2}}),
        ):
            result = session.act(actor, command)
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        hand = len(session.state.players['A'].zones['hand'])
        result = session.act('pilot:A', good)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertEqual(('battlefield', True), (session.state.cards[payer.object_id].zone, session.state.cards[payer.object_id].tapped))
        self.assertEqual(3, session.state.cards[foreign.object_id].counters['+1/+1'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_fresh_shapers_witness_kills_omitted_counter_payment(self):
        method = 'test_actual_shapers_uses_tapped_controlled_payer_and_rolls_back_bad_or_unfunded_selections'
        with patch('quorune.counter_removal.commit_counter_removals', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def pay_activation(self, session, source, payer, targets=()):
        action = self.selected_action(session, source)
        result = session.act('pilot:A', {'action_id': action['id'],
            'cost_cards': [session.state.cards[payer.object_id].ref], 'targets': list(targets), 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)

    def test_actual_bolrac_source_can_pay_both_its_tap_and_distinct_counter_resource(self):
        session = self.session(734030)
        source = self.add(session.engine, 'Bolrac-Clan Crusher')
        source.counters['+1/+1'] = 1
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session)
        self.checkpoint(session)
        action = self.selected_action(session, source)
        self.assertIn(source.ref, action['choice_schema']['cost_cards']['legal_refs'])
        self.pay_activation(session, source, source, ['D'])
        current = session.state.cards[source.object_id]
        self.assertTrue(current.tapped)
        self.assertEqual(0, current.counters.get('+1/+1', 0))
        self.assertEqual(40, session.state.players['D'].life)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([40, 40, 40, 38], [session.state.players[seat].life for seat in 'ABCD'])
        self.private_replay(session, private)

    def test_actual_ghave_uses_controlled_foreign_owned_payer_then_its_existing_token_sacrifice(self):
        session = self.session(734040)
        source = self.add(session.engine, 'Ghave, Guru of Spores', zone='hand')
        payer = self.add(session.engine, 'Generic Bound Body', ref='ghave-borrowed-payer')
        payer.owner = 'B'
        payer.counters['+1/+1'] = 1
        payer.tapped = True
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 4, 'W': 1, 'B': 1, 'G': 1})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.pay_activation(session, source, payer)
        self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertEqual(('battlefield', 'B', 'A', True),
            tuple(getattr(session.state.cards[payer.object_id], key) for key in ('zone', 'owner', 'controller', 'tapped')))
        self.assertIsNone(self.resolve(session))
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(token.object_id, field) for field in ('power', 'toughness')))
        self.assertIn('saproling', session.engine._type_parts(session.engine._effective_card_data(token)['type_line'])[1])
        action = next(row for row in self.actions(session) if row['id'].startswith('activate:' + session.state.cards[source.object_id].ref + ':')
            and any(choice.get('k') in {'sacrifice', 'sacrifice_one'} for choice in row['cost_summary'].get('choose_cost', ())))
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [token.ref],
            'targets': [session.state.cards[payer.object_id].ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertFalse(any(card.is_token and card.zone == 'battlefield' for card in session.state.cards.values()))
        self.assertEqual(5, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.private_replay(session, private)

    def test_actual_spike_rogue_removal_is_not_multiplied_but_its_result_uses_hardened_scales(self):
        session = self.session(734050)
        source = self.add(session.engine, 'Spike Rogue')
        source.counters['+1/+1'] = 2
        payer = self.add(session.engine, 'Generic Bound Body', ref='rogue-counter-payer')
        payer.counters['+1/+1'] = 1
        scales = self.add(session.engine, 'Hardened Scales')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assertTrue(self.binding(session, scales)['strict_capability_ready'])
        self.prepare(session, mana={'C': 2})
        self.checkpoint(session)
        self.pay_activation(session, source, payer)
        self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
        self.assertEqual(2, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.private_replay(session, private)

    def test_actual_sunstar_and_korozda_keep_their_ordinary_targeted_results(self):
        for index, name in enumerate(('Sunstar Chaplain', 'Korozda Gorgon')):
            session = self.session(734060 + index)
            source = self.add(session.engine, name)
            payer = self.add(session.engine, 'Generic Bound Body', ref='ordinary-counter-payer')
            payer.counters['+1/+1'] = 1
            target = self.add(session.engine, 'Generic Bound Body', seat='D', ref='ordinary-counter-target')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.prepare(session, mana={'C': 2})
            self.checkpoint(session)
            self.pay_activation(session, source, payer, [target.ref])
            self.assertEqual(0, session.state.cards[payer.object_id].counters.get('+1/+1', 0))
            self.assertIsNone(self.resolve(session))
            if name == 'Sunstar Chaplain':
                self.assertTrue(session.state.cards[target.object_id].tapped)
            else:
                self.assertEqual((1, 5), tuple(session.engine._numeric_stat(target.object_id, field) for field in ('power', 'toughness')))
            self.private_replay(session, private)

    def test_actual_ray_mutagen_produces_the_counter_then_pays_it_for_a_draw(self):
        session = self.session(734070)
        source = self.add(session.engine, 'Ray Fillet, Man Ray', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 10, 'W': 2, 'U': 2, 'B': 2, 'R': 2, 'G': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.assertIsNone(self.resolve(session))
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        action = next(row for row in self.actions(session) if row['id'].startswith('activate:' + token.ref + ':'))
        result = session.act('pilot:A', {'action_id': action['id'],
            'targets': [session.state.cards[source.object_id].ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        hand = len(session.state.players['A'].zones['hand'])
        self.pay_activation(session, source, source)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertEqual(hand + 1, len(session.state.players['A'].zones['hand']))
        self.private_replay(session, private)

    def test_paid_payer_last_counter_death_makes_its_target_illegal_without_refund(self):
        session = self.session(734080)
        source = self.add(session.engine, 'Korozda Gorgon')
        payer = self.add(session.engine, 'Spike Rogue', ref='zero-payer-target')
        payer.counters['+1/+1'] = 1
        unselected = self.add(session.engine, 'Generic Bound Body', seat='D', ref='unchanged-counter-body')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'C': 2})
        self.checkpoint(session)
        self.pay_activation(session, source, payer, [payer.ref])
        self.assertEqual('graveyard', session.state.cards[payer.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertTrue(session.state.stack)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[payer.object_id].zone)
        self.assertEqual((2, 6), tuple(session.engine._numeric_stat(unselected.object_id, field) for field in ('power', 'toughness')))
        self.private_replay(session, private)

    def test_runtime_cannot_omit_selected_payment_authority(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(734090)
        source = self.add(session.engine, 'Shapers of Nature')
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if program.cost_schema and any(choice.get('k') == SELECTED_PLUS_COUNTER_COST_KIND
                for choice in program.cost_schema.get('choices', ())))
        deps = tuple(cap for cap in program.capability_dependencies if cap != SELECTED_COUNTER_COST_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + SELECTED_COUNTER_COST_CAPABILITY, bound['blockers'])

    def test_unrepresented_amounts_subjects_and_counter_kinds_remain_residual(self):
        card = self.db.lookup('Shapers of Nature')
        for text in (
            card.oracle_text.replace('Remove a +1/+1 counter', 'Remove two +1/+1 counters'),
            card.oracle_text.replace('from a creature you control', 'from a permanent you control'),
            card.oracle_text.replace('from a creature you control', 'from a creature an opponent controls'),
            card.oracle_text.replace('Remove a +1/+1 counter', 'Remove a charge counter'),
            card.oracle_text + '\nWhenever a player sneezes, draw a card.',
        ):
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=text),
                capability_registry=self.registry, capability_profile='commander_review').status, text)

    def test_fresh_original_admission_kills_omitted_selected_cost_compiler(self):
        method = 'test_seven_originals_have_whole_admission_and_authoritative_payment_forms'
        with patch('quorune.compiler.activated_counter_costs.fixed_selected_counter_activation', side_effect=lambda ability: ability):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
