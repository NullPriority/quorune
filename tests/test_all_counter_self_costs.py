from __future__ import annotations

"""CR122/601.2f: intrinsic prices count each current player/permanent counter."""

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
from quorune.counter_snapshot import COUNTER_TOTAL_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.self_cost_public_counters import CAPABILITY
from quorune.self_cast_reductions import CastReductionMetric, CastReductionMetricKind, SelfCastReductionError
from scripts.build_test_database import build_fixture_database
import test_turn_count_self_costs as controls


class AllCounterSelfCostTests(unittest.TestCase):
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
    price = controls.TurnCountSelfCostTests.price

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'all-counter-self-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/all-counter-self-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('All current public counters',
            [DeckEntry('Generic Bound Commander', 1, 'commander'),
             DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_player_and_permanent_placements_reduce_price_and_source_enters_tapped(self):
        session = self.session(743100)
        source = self.add(session.engine, 'Lumbering Megasloth', zone='hand')
        turtle = self.add(session.engine, 'Thriving Turtle', zone='hand')
        prologue = self.add(session.engine, 'Prologue to Phyresis', zone='hand')
        growth = self.add(session.engine, 'Battlegrowth', zone='hand')
        body = self.add(session.engine, 'Generic Bound Body', seat='C')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U': 2, 'G': 3, 'C': 12})
        self.checkpoint(session)
        self.assertEqual({'GENERIC': 10, 'G': 2}, self.price(session, source))
        self.cast(session, 'A', turtle)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.players['A'].energy)
        self.cast(session, 'A', prologue)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([1, 1, 1], [session.state.players[s].poison for s in 'BCD'])
        self.cast(session, 'A', growth, [body.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'+1/+1': 1}, session.state.cards[body.object_id].counters)
        self.assertEqual({'GENERIC': 4, 'G': 2}, self.price(session, source))
        command = {'action_id': 'cast:' + source.ref, 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.cast(session, 'A', source)
        self.assertEqual(7, session.state.players['A'].mana_pool['C'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        landed = session.state.cards[source.object_id]
        self.assertEqual('battlefield', landed.zone)
        self.assertTrue(landed.tapped)
        effective = session.engine._effective_card_data(landed)
        self.assertIn('Trample', effective['keywords'])
        self.assertEqual(12, effective['mana_value'])
        self.assertEqual(8, session.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(8, session.engine._numeric_stat(source.object_id, 'toughness'))
        self.private_replay(session, private)

    def test_original_has_complete_nonempty_admitted_price_program(self):
        session = self.session(743101)
        source = self.add(session.engine, 'Lumbering Megasloth', zone='hand')
        ir = compile_oracle_card(self.db.lookup('Lumbering Megasloth'), capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertTrue(self.binding(session, source)['strict_capability_ready'])
        self.assertTrue(any(p.event == 'cast.cost.modify' and p.handlers
            for p in session.engine.semantics.programs_for_oracle(source.oracle_id)))

    def test_scalar_metric_has_a_closed_version2_shape_and_malformed_readers_fail_closed(self):
        metric = CastReductionMetric(kind=CastReductionMetricKind.PUBLIC_COUNTER_TOTAL, schema_version=2)
        self.assertEqual(metric, CastReductionMetric.from_dict(metric.to_dict()))
        descriptor = self_spell_cost_reduction_handler(
            self.db.lookup('Lumbering Megasloth').oracle_text.splitlines()[0])[1]
        for change in ({'schema_version': 1}, {'schema_version': True}, {'schema_version': 3},
                {'minimum': 0}, {'maximum': 10}, {'require_all': True}, {'counter_name': 'charge'},
                {'color': 'G'}, {'future': True}):
            with self.subTest(change=change):
                with self.assertRaises(SelfCastReductionError):
                    CastReductionMetric.from_dict({**metric.to_dict(), **change})
                modified = json.loads(json.dumps(descriptor))
                modified['reduction']['terms'][0]['metric'].update(change)
                candidate = SimpleNamespace(handlers=(modified,), effects=(), target_schema=None,
                    coverage=(), cost_schema=None, event_condition=None, event='cast.cost.modify', active_zone='all')
                _, blockers = program_dynamic_runtime_requirements(candidate)
                self.assertTrue(any('self_cost_public_counters:invalid' in b for b in blockers), blockers)
        for text in ('This spell costs {1} less to cast for each counter on players.',
                'This spell costs {1} less to cast for each counter among players and cards.',
                'This spell costs {1} less to cast for each counter among players and permanents you control.',
                'This spell costs {1} less to cast for each poison counter among players and permanents.'):
            self.assertIsNone(self_spell_cost_reduction_handler(text), text)

    def test_counter_and_base_price_authorities_cannot_be_withdrawn(self):
        session = self.session(743102)
        source = self.add(session.engine, 'Lumbering Megasloth', zone='hand')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'cast.cost.modify']
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict'])
        for cap in (CAPABILITY, COUNTER_TOTAL_CAPABILITY, 'casting.cost.self_public_reduction'):
            with self.subTest(cap=cap):
                deps = tuple(x for x in program.capability_dependencies if x != cap)
                missing = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])
                raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(x for x in raw['capabilities'] if x['id'] == cap).update(status='blocked', blockers=['withdrawn public counter price'])
                ir = compile_oracle_card(self.db.lookup('Lumbering Megasloth'),
                    capability_registry=CapabilityRegistry(raw), capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)
                self.assertTrue(ir.material_residuals)

    def test_current_public_total_excludes_other_zones_and_phasing_and_keeps_colored_floor(self):
        session = self.session(743103)
        source = self.add(session.engine, 'Lumbering Megasloth', zone='hand')
        own = self.add(session.engine, 'Generic Bound Body', ref='counter-scope-own')
        foreign = self.add(session.engine, 'Generic Bound Body', seat='B', ref='counter-scope-foreign')
        own.counters['charge'] = 2
        foreign.counters['charge'] = 3
        for index, zone in enumerate(('hand', 'graveyard', 'exile')):
            excluded = self.add(session.engine, 'Generic Bound Body', seat='C', zone=zone, ref='excluded-zone-' + str(index))
            excluded.counters['charge'] = 30
        phased = self.add(session.engine, 'Generic Bound Body', seat='D', ref='counter-scope-phased')
        phased.counters['charge'] = 50
        phased.phased_out = True
        source.counters['charge'] = 20
        session.state.players['A'].poison = 1
        session.state.players['B'].energy = 2
        session.state.players['C'].counters['experience'] = 2
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'G': 2})
        self.checkpoint(session)
        self.assertEqual({'G': 2}, self.price(session, source))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A', {'action_id': 'cast:' + source.ref, 'pay': {}}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source)
        self.assertEqual(0, session.state.players['A'].mana_pool['G'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.private_replay(session, private)

    def test_actual_bounce_removes_departed_permanent_counters_from_current_price(self):
        session = self.session(743104)
        source = self.add(session.engine, 'Lumbering Megasloth', zone='hand')
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        foreign = self.add(session.engine, 'Generic Bound Body', seat='B')
        foreign.counters['charge'] = 3
        session.state.players['C'].energy = 2
        self.prepare(session, mana={'U': 2, 'G': 2, 'C': 8})
        self.checkpoint(session)
        self.assertEqual({'GENERIC': 5, 'G': 2}, self.price(session, source))
        self.cast(session, 'A', bounce, [foreign.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[foreign.object_id].zone)
        self.assertEqual({}, session.state.cards[foreign.object_id].counters)
        self.assertEqual({'GENERIC': 8, 'G': 2}, self.price(session, source))
        self.cast(session, 'A', source)
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session, load=True)

    def test_counter_bearing_mana_source_departure_cannot_change_locked_price(self):
        session = self.session(743105)
        source = self.add(session.engine, 'Lumbering Megasloth', zone='hand')
        donor = self.add(session.engine, 'Wild Cantor')
        donor.counters['charge'] = 3
        session.state.players['B'].energy = 2
        self.prepare(session, mana={'G': 1, 'C': 5})
        self.checkpoint(session)
        self.assertEqual({'GENERIC': 5, 'G': 2}, self.price(session, source))
        self.cast(session, 'A', source)
        self.assertEqual('graveyard', session.state.cards[donor.object_id].zone)
        self.assertEqual({}, session.state.cards[donor.object_id].counters)
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        self.assertEqual(0, session.state.players['A'].mana_pool['G'])
        self.assertEqual('stack', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session, load=True)

    def test_fresh_original_kills_omitted_public_counter_total(self):
        method = 'test_actual_player_and_permanent_placements_reduce_price_and_source_enters_tapped'
        with patch('quorune.self_cast_reductions.public_player_permanent_counter_total', return_value=0):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_original_kills_omitted_counter_price_production(self):
        method = 'test_original_has_complete_nonempty_admitted_price_program'
        with patch('quorune.compiler.cast_cost_modifier_templates._object_count_metric', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()
