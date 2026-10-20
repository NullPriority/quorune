from __future__ import annotations

"""CR107.3/601.2f: current numeric qualifiers reduce payment, never announced X."""

import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
from quorune.compiler.cast_cost_modifier_templates import self_spell_cost_reduction_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.self_cost_numeric_counts import CAPABILITY, self_cost_numeric_count_dependencies
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.semantic_runtime.cast_costs import SelfSpellCostReductionHandler
from scripts.build_test_database import build_fixture_database
import test_turn_count_self_costs as controls


class NumericCountSelfCostTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.TurnCountSelfCostTests.session
    add = controls.TurnCountSelfCostTests.add
    prepare = controls.TurnCountSelfCostTests.prepare
    checkpoint = controls.TurnCountSelfCostTests.checkpoint
    cast = controls.TurnCountSelfCostTests.cast
    resolve = controls.TurnCountSelfCostTests.resolve
    replay = controls.TurnCountSelfCostTests.replay
    binding = controls.TurnCountSelfCostTests.binding
    actions = controls.TurnCountSelfCostTests.actions
    priority = controls.TurnCountSelfCostTests.priority
    private_replay = controls.TurnCountSelfCostTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'numeric-count-self-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/numeric-count-self-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Current numeric count prices',
            [DeckEntry('Generic Bound Commander', 1, 'commander'),
             DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_growth_reduces_cast_payment_and_retains_two_mana_counter_demand(self):
        self.counter_witness(743200)

    def test_actual_humble_loses_discount_and_opponent_can_pay_original_x(self):
        self.counter_witness(743201, suppress=True, paid=True)

    def test_two_inclusive_power_creatures_zero_generic_but_keep_chosen_x(self):
        self.counter_witness(743202, floor=True)

    def counter_witness(self, seed, *, suppress=False, floor=False, paid=False):
        session = self.session(seed)
        source = self.add(session.engine, 'Spectral Denial', zone='hand')
        growth = self.add(session.engine, 'Giant Growth', zone='hand')
        body = self.add(session.engine, 'Grizzly Bears')
        second = self.add(session.engine, 'Grizzly Bears', ref='nonqualifying-own-creature')
        self.add(session.engine, 'Generic Bound Body', ref='high-toughness-only')
        foreign = self.add(session.engine, 'Grizzly Bears', seat='B', ref='high-power-foreign-creature')
        foreign.counters['+1/+1'] = 6
        phased = self.add(session.engine, 'Grizzly Bears', ref='high-power-phased-creature')
        phased.counters['+1/+1'] = 6
        phased.phased_out = True
        if floor:
            body.counters['+1/+1'] = 2
            second.counters['+1/+1'] = 2
        humble = self.add(session.engine, 'Humble', zone='hand') if suppress else None
        victim = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U': 1, 'G': 1, 'W': 1, 'C': 3 if suppress else 2})
        session.state.players['B'].mana_pool.update(R=1,C=2)
        self.checkpoint(session)
        if not floor:
            self.cast(session, 'A', growth, [body.ref])
            self.assertIsNone(self.resolve(session))
            self.assertEqual(5, session.engine._numeric_stat(body.object_id, 'power'))
        if suppress:
            self.cast(session, 'A', humble, [body.ref])
            self.assertIsNone(self.resolve(session))
            self.assertEqual(3, session.engine._numeric_stat(body.object_id, 'power'))
        self.priority(session, 'B')
        self.cast(session, 'B', victim, ['D'])
        target = session.state.stack[-1].ref
        self.priority(session, 'A')
        before = authoritative_state_hash(session.state)
        command = {'action_id': 'cast:' + source.ref, 'targets': [target], 'x': 2, 'pay': 'auto'}
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0 if suppress else 2 if floor else 1, session.state.players['A'].mana_pool['C'])
        self.assertEqual(0, session.state.players['A'].mana_pool['U'])
        self.assertEqual(2, session.state.stack[-1].x_value)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        session = self.replay(session, load=True)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(['pilot:B'], session.pending_principals())
        packet = session.packet('pilot:B', full=True)['decision']
        self.assertEqual(2, packet['ctx']['cost']['GENERIC'])
        self.assertEqual([True, False], packet['legal_actions'][0]['choice_schema']['legal_values'])
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id':'choose','pay':False}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:B', {'action_id':'choose','pay':'false'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:B', {'action_id':'choose','pay':paid})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.stack)
        self.assertEqual(37 if paid else 40, session.state.players['D'].life)
        self.assertEqual(0 if paid else 2, session.state.players['B'].mana_pool['C'])
        self.private_replay(session, private)

    def test_original_numeric_program_is_complete_and_nonempty(self):
        session = self.session(743203)
        source = self.add(session.engine, 'Spectral Denial', zone='hand')
        ir = compile_oracle_card(self.db.lookup('Spectral Denial'), capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'cast.cost.modify']
        self.assertTrue(program.handlers)
        self.assertIn(CAPABILITY, program.capability_dependencies)
        metric = SelfSpellCostReductionHandler().validate(program.handlers[0]).terms[0].metric
        self.assertEqual('controller_zone', metric.queries[0].scope.value)
        self.assertEqual({'characteristic':'power','comparison':'at_least','value':4},
            metric.queries[0].query.numeric_characteristic.to_dict())

    def test_numeric_count_grammar_and_typed_queries_are_closed(self):
        for stat in ('power','toughness'):
            for bound in ('greater','less'):
                text = f'This spell costs {{1}} less to cast for each creature you control with {stat} 4 or {bound}.'
                descriptor = self_spell_cost_reduction_handler(text)[1]
                metric = SelfSpellCostReductionHandler().validate(descriptor).terms[0].metric
                self.assertEqual((CAPABILITY,), self_cost_numeric_count_dependencies(metric.to_dict()))
                query = metric.queries[0].query
                self.assertEqual(query, ObjectQuerySpec.from_dict(query.to_dict()))
                self.assertEqual(stat, query.numeric_characteristic.characteristic.value)
                self.assertEqual('at_least' if bound == 'greater' else 'at_most', query.numeric_characteristic.comparison.value)
        descriptor = self_spell_cost_reduction_handler(self.db.lookup('Spectral Denial').oracle_text.splitlines()[0])[1]
        for change in ('scope','zone','noncreature','bool','future','power_or_toughness','total_power_and_toughness'):
            modified = json.loads(json.dumps(descriptor))
            row = modified['reduction']['terms'][0]['metric']['queries'][0]
            if change == 'scope': row['scope'] = 'all_zones'
            elif change == 'zone': row['query']['zones'] = ['graveyard']
            elif change == 'noncreature': row['query']['types_all'] = []
            elif change == 'bool': row['query']['numeric_characteristic']['value'] = True
            elif change in {'power_or_toughness','total_power_and_toughness'}:
                row['query']['numeric_characteristic']['characteristic'] = change
            else: row['query']['numeric_characteristic']['future'] = True
            candidate = SimpleNamespace(handlers=(modified,), effects=(), target_schema=None,
                coverage=(), cost_schema=None, event_condition=None, event='cast.cost.modify', active_zone='all')
            _, blockers = program_dynamic_runtime_requirements(candidate)
            self.assertTrue(any('runtime_self_cost_numeric_count:invalid' in b for b in blockers), (change, blockers))
        for phrase in ('power X or greater', 'power -1 or greater', 'power 4 or greater that attacked this turn',
                'power equal to its toughness', 'power 4 or greater among all players'):
            self.assertIsNone(self_spell_cost_reduction_handler(
                'This spell costs {1} less to cast for each creature you control with '+phrase+'.'))

    def test_numeric_and_base_price_authorities_cannot_be_withdrawn(self):
        session = self.session(743204)
        source = self.add(session.engine, 'Spectral Denial', zone='hand')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'cast.cost.modify']
        for cap in (CAPABILITY, 'casting.cost.self_public_reduction'):
            with self.subTest(cap=cap):
                deps = tuple(x for x in program.capability_dependencies if x != cap)
                missing = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+cap, bound['blockers'])
                raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(r for r in raw['capabilities'] if r['id'] == cap).update(status='blocked', blockers=['withdrawn numeric price authority'])
                ir = compile_oracle_card(self.db.lookup('Spectral Denial'), capability_registry=CapabilityRegistry(raw),
                    capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)

    def test_fresh_original_kills_dropped_numeric_qualifier(self):
        original = ObjectQuerySpec.to_dict
        def omit_numeric(query):
            result = original(query)
            if result.get('numeric_characteristic') is not None:
                result['numeric_characteristic'] = None
            return result
        method = 'test_actual_growth_reduces_cast_payment_and_retains_two_mana_counter_demand'
        with patch.object(ObjectQuerySpec, 'to_dict', omit_numeric):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_original_kills_dropped_numeric_price_production(self):
        method = 'test_original_numeric_program_is_complete_and_nonempty'
        with patch('quorune.compiler.cast_cost_modifier_templates._numeric_creature_count_metric', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
