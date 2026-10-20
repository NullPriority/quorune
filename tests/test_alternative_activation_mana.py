from __future__ import annotations

"""CR602.2b: choose one complete printed activation cost and one source tap."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import card_semantic_status
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_compound_damage_recipients as controls

NAMES = ('Skeleton Shard', 'Heartwood Shard', 'Pearl Shard', 'Granite Shard')
COLORS = ('B', 'G', 'W', 'R')


class AlternativeActivationManaTests(unittest.TestCase):
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
        path = Path(cls.temporary.name) / 'alternative-activation.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/alternative-activation-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Alternative activation costs',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def action(self, session, source):
        current = session.state.cards[source.object_id]
        return next(row for row in self.actions(session) if row['id'].startswith('activate:' + current.ref + ':'))

    def test_four_originals_preserve_raw_cost_and_expose_two_complete_owned_mana_vectors(self):
        for index, (name, color) in enumerate(zip(NAMES, COLORS)):
            session = self.session(736000 + index)
            source = self.add(session.engine, name)
            self.add(session.engine, 'Ornithopter', zone='graveyard', ref='alternative-grave-target')
            self.add(session.engine, 'Generic Bound Body', ref='alternative-body-target')
            self.prepare(session, mana={'C': 3, color: 1})
            card = self.db.lookup(name)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertTrue(self.binding(session, source)['strict_capability_ready'])
            status = card_semantic_status(card, session.engine.semantics, db=self.db,
                capability_registry=self.registry, capability_profile='commander_review')
            self.assertEqual('fully_playable', status['status'])
            ability, = compile_activated_ability_catalog(card)['front']
            self.assertTrue(ability.tap_source)
            self.assertEqual(card.oracle_text.split(':', 1)[0], ability.cost_text)
            self.assertEqual({(3, 0), (0, 1)}, {(option.requirements['GENERIC'], option.requirements[color]) for option in ability.mana_cost_options})
            self.assertEqual(2, len(self.action(session, source)['cost_options']))

    def test_actual_granite_pays_either_arm_once_and_rolls_back_forgery_wrong_color_and_overpayment(self):
        for index, colored in enumerate((False, True)):
            session = self.session(736020 + index)
            source = self.add(session.engine, 'Granite Shard')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.prepare(session, mana={'C': 3, 'R': 1, 'G': 1})
            self.checkpoint(session)
            action = self.action(session, source)
            option = next(option for option in action['cost_options'] if option['requirements']['R'] == int(colored))
            payment = {'R': 1} if colored else {'C': 3}
            good = {'action_id': action['id'], 'cost_option': option['id'], 'pay': 'manual', 'payment': payment, 'targets': ['D']}
            before = authoritative_state_hash(session.state)
            for actor, command in (('pilot:B', good), ('pilot:A', {**good, 'cost_option': 'forged'}),
                                   ('pilot:A', {**good, 'payment': {'G': 1}}),
                                   ('pilot:A', {**good, 'payment': {'C': 3, 'R': 1}})):
                result = session.act(actor, command)
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
            result = session.act('pilot:A', good)
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(session.state.cards[source.object_id].tapped)
            self.assertEqual(40, session.state.players['D'].life)
            self.assertEqual(4 if colored else 2, sum(session.state.players['A'].mana_pool.values()))
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual([40, 40, 40, 39], [session.state.players[seat].life for seat in 'ABCD'])
            self.private_replay(session, private)

    def test_actual_colored_shard_arms_return_grant_and_prevent_through_their_existing_result_owners(self):
        for index, (name, color) in enumerate(zip(NAMES[:3], COLORS[:3])):
            session = self.session(736030 + index)
            source = self.add(session.engine, name)
            target = self.add(session.engine, 'Generic Bound Body', ref='alternative-target')
            grave = self.add(session.engine, 'Ornithopter', zone='graveyard', ref='alternative-artifact-creature')
            response = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            session.state.players['B'].mana_pool['R'] = 1
            self.prepare(session, mana={color: 1})
            self.checkpoint(session)
            action = self.action(session, source)
            option, = action['cost_options']
            selected = [grave.ref] if index == 0 else [target.ref] if index == 1 else ['D']
            result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'],
                'targets': selected, 'pay': 'auto'})
            self.assertTrue(result.ok, result.summary)
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            if index == 0:
                self.assertEqual('hand', session.state.cards[grave.object_id].zone)
            elif index == 1:
                self.assertIn('trample', session.engine._combat_keywords(session.state.cards[target.object_id]))
            else:
                self.cast(session, 'B', response, ['D'])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(39, session.state.players['D'].life)
            self.assertTrue(session.state.cards[source.object_id].tapped)
            self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session, private)

    def test_unsupported_resource_branches_variable_symbols_and_independent_result_stay_residual(self):
        card = self.db.lookup('Granite Shard')
        for cost in ('{3}, {T} or {R}, Sacrifice this artifact', '{X}, {T} or {R}, {T}',
                     '{3}, {T} or {R}, {Q}', '{3}, {T} or {3}, {T}'):
            text = cost + ': This artifact deals 1 damage to any target.'
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=text),
                capability_registry=self.registry, capability_profile='commander_review').status)
        crystal = self.db.lookup('Crystal Shard')
        self.assertNotEqual('exact', compile_oracle_card(crystal, capability_registry=self.registry,
            capability_profile='commander_review').status)

    def test_fresh_actual_granite_witness_kills_omitted_alternative_cost_production(self):
        method = 'test_four_originals_preserve_raw_cost_and_expose_two_complete_owned_mana_vectors'
        with patch('quorune.compiler.activation_mana_costs.fixed_alternative_activation_mana_cost', side_effect=lambda ability: ability):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_actual_suppression_field_increase_applies_separately_to_both_arms(self):
        for index, colored in enumerate((False, True)):
            session = self.session(736050 + index)
            source = self.add(session.engine, 'Granite Shard')
            tax = self.add(session.engine, 'Suppression Field', seat='B')
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.assertTrue(self.binding(session, tax)['strict_capability_ready'])
            payment = {'C': 2, 'R': 1} if colored else {'C': 5}
            self.prepare(session, mana=payment)
            self.checkpoint(session)
            action = self.action(session, source)
            option = next(row for row in action['cost_options'] if row['requirements']['R'] == int(colored))
            self.assertEqual(2 if colored else 5, option['requirements']['GENERIC'])
            result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'],
                'pay': 'manual', 'payment': payment, 'targets': ['D']})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
            self.assertTrue(session.state.cards[source.object_id].tapped)
            session = self.replay(session, load=True)
            self.assertIsNone(self.resolve(session))
            self.assertEqual(39, session.state.players['D'].life)
            self.private_replay(session, private)

    def test_tapped_source_is_unavailable_and_target_departure_does_not_refund_the_paid_arm(self):
        session = self.session(736060)
        source = self.add(session.engine, 'Granite Shard')
        source.tapped = True
        self.prepare(session, mana={'C': 3, 'R': 1})
        self.assertFalse(any(row['id'].startswith('activate:' + source.ref + ':') for row in self.actions(session)))
        session = self.session(736061)
        source = self.add(session.engine, 'Heartwood Shard')
        target = self.add(session.engine, 'Generic Bound Body')
        response = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
        # Return the creature target: payment remains spent when the only target becomes illegal.
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['U'] = 1
        self.prepare(session, mana={'G': 1})
        self.checkpoint(session)
        action = self.action(session, source)
        option, = action['cost_options']
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'],
            'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, 'B', response, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[target.object_id].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session, private)

    def test_runtime_cannot_omit_the_mana_option_owner(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session = self.session(736070)
        source = self.add(session.engine, 'Granite Shard')
        program = next(program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if program.cost_schema and program.cost_schema.get('mana_cost_options'))
        cap = 'activation.mana_cost.fixed_complex'
        deps = tuple(value for value in program.capability_dependencies if value != cap)
        missing = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])

    def test_mana_option_dependency_reader_rejects_incomplete_or_duplicate_descriptors(self):
        from types import SimpleNamespace
        from quorune.activation_mana_cost import activation_mana_option_runtime_dependencies
        ability, = compile_activated_ability_catalog(self.db.lookup('Granite Shard'))['front']
        rows = [option.to_dict() for option in ability.mana_cost_options]
        for event, options in (('spell', rows), ('activate', {'bad': rows}),
                               ('activate', [rows[0], rows[0]]),
                               ('activate', [{**rows[0], 'extra': True}])):
            with self.assertRaises(ValueError):
                activation_mana_option_runtime_dependencies(SimpleNamespace(
                    event=event, cost_schema={'mana_cost_options': options}))

    def test_actual_source_departure_keeps_the_paid_ability_and_its_damage(self):
        session = self.session(736062)
        source = self.add(session.engine, 'Granite Shard')
        response = self.add(session.engine, 'Boomerang', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'R': 1})
        self.checkpoint(session)
        action = self.action(session, source)
        option, = action['cost_options']
        result = session.act('pilot:A', {'action_id': action['id'], 'cost_option': option['id'],
            'targets': ['D'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, 'B', response, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(39, session.state.players['D'].life)
        self.private_replay(session, private)
