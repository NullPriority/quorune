from __future__ import annotations

"""CR601.2f/700.4: source spell prices use complete public turn counts."""

import json
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
from quorune.compiler.cast_cost_modifier_templates import self_spell_cost_reduction_handler
from quorune.self_cast_reductions import (
    CastReductionMetric, CastReductionMetricKind, CastReductionTurnFact, SelfCastReductionError,
    cast_reduction_multiplier,
)
from quorune.rules.self_cost_turn_counts import CAPABILITY
from quorune.semantic_runtime.cast_costs import SelfSpellCostReductionHandler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_self_cast_restrictions as controls


NAMES = ('Diregraf Rebirth', 'Delightful Discovery', 'Inspired Fire')


class TurnCountSelfCostTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.SelfCastRestrictionTests.session
    add = controls.SelfCastRestrictionTests.add
    prepare = controls.SelfCastRestrictionTests.prepare
    checkpoint = controls.SelfCastRestrictionTests.checkpoint
    cast = controls.SelfCastRestrictionTests.cast
    resolve = controls.SelfCastRestrictionTests.resolve
    replay = controls.SelfCastRestrictionTests.replay
    binding = controls.SelfCastRestrictionTests.binding
    actions = controls.SelfCastRestrictionTests.actions
    priority = controls.SelfCastRestrictionTests.priority
    private_replay = controls.SelfCastRestrictionTests.private_replay
    advance = controls.SelfCastRestrictionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'turn-count-self-costs.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/turn-count-self-cost-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public turn-count prices',
            [DeckEntry('Generic Bound Commander', 1, 'commander'),
             DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def price(self, session, source):
        row = next(row for row in self.actions(session) if row['id'] == 'cast:' + source.ref)
        price = next(option['requirements'] for option in row['cost_options'] if option['id'] == 'normal')
        return {key: value for key, value in price.items() if value}

    def test_actual_rebirth_counts_a_real_death_and_returns_the_selected_creature(self):
        session = self.session(742100)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='hand')
        self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='rebirth-initial-legal-target')
        victim = self.add(session.engine, 'Grizzly Bears', ref='rebirth-death')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'B': 1, 'G': 1, 'R': 1, 'C': 3})
        self.checkpoint(session)
        self.assertEqual({'GENERIC': 3, 'B': 1, 'G': 1}, dict(self.price(session, source)))
        self.cast(session, 'A', bolt, [victim.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.assertEqual({'GENERIC': 2, 'B': 1, 'G': 1}, dict(self.price(session, source)))
        command = {'action_id': 'cast:' + source.ref, 'targets': [victim.ref], 'pay': 'auto'}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players['A'].mana_pool['C'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[victim.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_three_originals_have_complete_admitted_cost_programs(self):
        for index, name in enumerate(NAMES):
            with self.subTest(name=name):
                session = self.session(742110 + index)
                source = self.add(session.engine, name, zone='hand')
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertTrue(self.binding(session, source)['strict_capability_ready'])
                self.assertTrue(any(p.event == 'cast.cost.modify' and p.handlers
                    for p in session.engine.semantics.programs_for_oracle(source.oracle_id)))

    def test_actual_discovery_counts_only_opponent_casts_and_completes_private_scry_draw(self):
        session = self.session(742120)
        source = self.add(session.engine, 'Delightful Discovery', zone='hand')
        foreign = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand', ref='discovery-foreign-cast')
        own = self.add(session.engine, 'Lightning Bolt', zone='hand', ref='discovery-own-cast')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U': 1, 'R': 1, 'C': 4})
        session.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(session)
        self.assertEqual({'GENERIC': 4, 'U': 1}, self.price(session, source))
        self.cast(session, 'A', own, ['D'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'GENERIC': 4, 'U': 1}, self.price(session, source))
        self.priority(session, 'B')
        self.cast(session, 'B', foreign, ['D'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'GENERIC': 3, 'U': 1}, self.price(session, source))
        before_hand = len(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', source)
        self.assertEqual(1, session.state.players['A'].mana_pool['C'])
        session = self.replay(session, load=True)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual(['pilot:A'], session.pending_principals())
        session = self.replay(session, load=True)
        packet = session.packet('pilot:A', full=True)['decision']
        refs = packet['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual(2, len(refs))
        for viewer in 'BCD':
            foreign_packet = json.dumps(session.packet('pilot:' + viewer, full=True))
            for ref in refs:
                self.assertNotIn(ref, foreign_packet)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D', {'action_id': 'choose',
            'cards': {'top': refs, 'bottom': []}}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id': 'choose', 'cards': {'top': refs, 'bottom': []}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before_hand + 1, len(session.state.players['A'].zones['hand']))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_actual_inspired_fire_counts_draws_without_changing_four_damage(self):
        session = self.session(742121)
        source = self.add(session.engine, 'Inspired Fire', zone='hand')
        draw = self.add(session.engine, 'Divination', zone='hand')
        target = self.add(session.engine, 'Generic Bound Body', seat='B')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.prepare(session, mana={'U': 1, 'R': 1, 'C': 5})
        self.checkpoint(session)
        self.assertEqual({'GENERIC': 3, 'R': 1}, self.price(session, source))
        self.cast(session, 'A', draw)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({'GENERIC': 1, 'R': 1}, self.price(session, source))
        self.cast(session, 'A', source, [target.ref])
        self.assertEqual(2, session.state.players['A'].mana_pool['C'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(4, session.state.cards[target.object_id].marked_damage)
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
        for object_id in session.state.players['A'].zones['hand']:
            for viewer in 'BCD':
                self.assertNotIn(session.state.cards[object_id].ref,
                    json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.private_replay(session, private)

    def test_fresh_rebirth_kills_omitted_counted_price(self):
        method = 'test_actual_rebirth_counts_a_real_death_and_returns_the_selected_creature'
        with patch('quorune.self_cast_reductions.self_cost_turn_count', return_value=0):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_fresh_rebirth_kills_omitted_counted_price_producer(self):
        from quorune.compiler import cast_cost_modifier_templates as compiler
        original = compiler._object_count_metric
        def omitted(text):
            return None if text == 'creature that died this turn' else original(text)
        method = 'test_actual_rebirth_counts_a_real_death_and_returns_the_selected_creature'
        with patch.object(compiler, '_object_count_metric', side_effect=omitted):
            with self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()

    def test_counted_metrics_require_v2_count_form_and_preserve_legacy_truth_payloads(self):
        for fact in (CastReductionTurnFact.CREATURE_DEATH_COUNT,
                CastReductionTurnFact.OPPONENT_SPELL_CAST_COUNT, CastReductionTurnFact.CONTROLLER_CARD_DRAW_COUNT):
            metric = CastReductionMetric(kind=CastReductionMetricKind.TURN_VALUE, turn_fact=fact, schema_version=2)
            self.assertEqual(metric, CastReductionMetric.from_dict(json.loads(json.dumps(metric.to_dict()))))
            for change in ({'schema_version': 1}, {'kind': CastReductionMetricKind.TURN_FACT},
                    {'minimum': 1}, {'color': 'G'}, {'schema_version': True}, {'require_all': True}):
                with self.subTest(fact=fact, change=change), self.assertRaises(SelfCastReductionError):
                    replace(metric, **change)
        legacy = CastReductionMetric(kind=CastReductionMetricKind.TURN_FACT,
            turn_fact=CastReductionTurnFact.CREATURE_DIED)
        self.assertEqual({'schema_version': 1, 'kind': 'turn_fact', 'queries': [], 'minimum': None,
            'maximum': None, 'require_all': False, 'color': None, 'turn_fact': 'creature_died'}, legacy.to_dict())
        draw_threshold = CastReductionMetric(kind=CastReductionMetricKind.CONTROLLER_DRAW_COUNT,
            minimum=2, schema_version=2)
        self.assertEqual(draw_threshold, CastReductionMetric.from_dict(draw_threshold.to_dict()))

    def test_counted_authority_and_base_cost_owner_cannot_be_withdrawn(self):
        session = self.session(742130)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='hand')
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'cast.cost.modify']
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict'])
        for cap in (CAPABILITY, 'casting.cost.self_public_reduction'):
            with self.subTest(cap=cap):
                deps = tuple(x for x in program.capability_dependencies if x != cap)
                missing = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])
                raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(x for x in raw['capabilities'] if x['id'] == cap).update(status='blocked', blockers=['withdrawn counted self cost'])
                for name in NAMES:
                    self.assertNotEqual('exact', compile_oracle_card(self.db.lookup(name),
                        capability_registry=CapabilityRegistry(raw), capability_profile='commander_review').status)

    def test_malformed_counted_metric_is_rejected_by_the_runtime_requirement_reader(self):
        descriptor = self_spell_cost_reduction_handler(
            self.db.lookup('Diregraf Rebirth').oracle_text.splitlines()[0])[1]
        for change in ({'schema_version': 1}, {'schema_version': True}, {'minimum': 1}, {'future': True}):
            modified = json.loads(json.dumps(descriptor))
            metric = modified['reduction']['terms'][0]['metric']
            metric.update(change)
            candidate = SimpleNamespace(handlers=(modified,), effects=(), target_schema=None,
                coverage=(), cost_schema=None, event_condition=None, event='cast.cost.modify', active_zone='all')
            with self.subTest(change=change):
                _, blockers = program_dynamic_runtime_requirements(candidate)
                self.assertTrue(any('self_cost_turn_count' in blocker for blocker in blockers), blockers)

    def test_actual_multiple_deaths_are_counts_and_do_not_reduce_colored_mana(self):
        session = self.session(742131)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='hand')
        initial = self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='multi-initial-target')
        victims = [self.add(session.engine, 'Grizzly Bears', seat=seat, ref='multi-death-' + seat) for seat in 'AB']
        bolts = [self.add(session.engine, 'Lightning Bolt', zone='hand', ref='multi-bolt-' + str(i)) for i in range(2)]
        self.prepare(session, mana={'R': 2, 'B': 1, 'G': 1, 'C': 3})
        self.checkpoint(session)
        for bolt, victim in zip(bolts, victims):
            self.cast(session, 'A', bolt, [victim.ref])
            self.assertIsNone(self.resolve(session))
        self.assertEqual({'GENERIC': 1, 'B': 1, 'G': 1}, self.price(session, source))
        counted = SelfSpellCostReductionHandler().validate(self_spell_cost_reduction_handler(
            self.db.lookup('Diregraf Rebirth').oracle_text.splitlines()[0])[1]).terms[0].metric
        legacy = CastReductionMetric(kind=CastReductionMetricKind.TURN_FACT,
            turn_fact=CastReductionTurnFact.CREATURE_DIED)
        self.assertEqual(2, cast_reduction_multiplier(session.engine, 'A', counted))
        self.assertEqual(1, cast_reduction_multiplier(session.engine, 'A', legacy))
        self.cast(session, 'A', source, [initial.ref])
        self.assertEqual(2, session.state.players['A'].mana_pool['C'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[initial.object_id].zone)

    def test_countered_opponent_spell_counts_but_current_discovery_does_not(self):
        session = self.session(742132)
        source = self.add(session.engine, 'Delightful Discovery', zone='hand')
        foreign = self.add(session.engine, 'Lightning Bolt', seat='B', zone='hand')
        counter = self.add(session.engine, 'Counterspell', zone='hand')
        self.prepare(session, mana={'U': 3, 'C': 4})
        session.state.players['B'].mana_pool['R'] = 1
        self.checkpoint(session)
        self.priority(session, 'B')
        self.cast(session, 'B', foreign, ['D'])
        target = session.state.stack[-1].ref
        self.priority(session, 'A')
        self.cast(session, 'A', counter, [target])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(40, session.state.players['D'].life)
        self.assertEqual('graveyard', session.state.cards[foreign.object_id].zone)
        self.assertEqual({'GENERIC': 3, 'U': 1}, self.price(session, source))
        self.cast(session, 'A', source)
        self.assertEqual(1, session.state.players['A'].mana_pool['C'])
        self.assertEqual('stack', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_flashback_receives_death_count_and_exiles_after_real_return(self):
        session = self.session(742133)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='graveyard', ref='rebirth-flashback-source')
        target = self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='flashback-return-target')
        victim = self.add(session.engine, 'Grizzly Bears', seat='B', ref='flashback-death-source')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        self.prepare(session, mana={'B': 1, 'G': 1, 'R': 1, 'C': 5})
        self.checkpoint(session)
        self.cast(session, 'A', bolt, [victim.ref])
        self.assertIsNone(self.resolve(session))
        row = next(row for row in self.actions(session) if row['id'] == 'cast:' + source.ref)
        option = next(x for x in row['cost_options'] if x['id'] == 'flashback')
        self.assertEqual(4, option['requirements']['GENERIC'])
        self.assertEqual((1, 1), (option['requirements']['B'], option['requirements']['G']))
        result = session.act('pilot:A', {'action_id': row['id'], 'cost_option': 'flashback',
            'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players['A'].mana_pool['C'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
        self.assertEqual('exile', session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_actual_draw_count_preserves_old_threshold_truth_and_turn_reset(self):
        session = self.session(742134)
        source = self.add(session.engine, 'Inspired Fire', zone='hand')
        draw = self.add(session.engine, 'Divination', zone='hand')
        self.add(session.engine, 'Generic Bound Body', seat='B')
        self.prepare(session, mana={'U': 1, 'R': 1, 'C': 5})
        self.checkpoint(session)
        counted = SelfSpellCostReductionHandler().validate(self_spell_cost_reduction_handler(
            self.db.lookup('Inspired Fire').oracle_text.splitlines()[0])[1]).terms[0].metric
        legacy = CastReductionMetric(kind=CastReductionMetricKind.CONTROLLER_DRAW_COUNT,
            minimum=2, schema_version=2)
        self.cast(session, 'A', draw)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, cast_reduction_multiplier(session.engine, 'A', counted))
        self.assertEqual(1, cast_reduction_multiplier(session.engine, 'A', legacy))
        self.assertEqual(0, cast_reduction_multiplier(session.engine, 'B', counted))
        turn = session.state.turn_sequence
        self.advance(session, lambda s: s.state.turn_sequence > turn)
        self.assertEqual(0, cast_reduction_multiplier(session.engine, 'A', counted))
        self.assertEqual(0, cast_reduction_multiplier(session.engine, 'A', legacy))
        self.replay(session, load=True)

    def test_generic_floor_after_four_real_deaths_keeps_both_colored_requirements(self):
        session = self.session(742135)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='hand')
        target = self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='floor-return-target')
        victims = [self.add(session.engine, 'Grizzly Bears', seat=seat, ref='floor-death-' + seat) for seat in 'ABCD']
        bolts = [self.add(session.engine, 'Lightning Bolt', zone='hand', ref='floor-bolt-' + str(i)) for i in range(4)]
        self.prepare(session, mana={'R': 4, 'B': 1, 'G': 1})
        self.checkpoint(session)
        for bolt, victim in zip(bolts, victims):
            self.cast(session, 'A', bolt, [victim.ref])
            self.assertIsNone(self.resolve(session))
        self.assertEqual({'B': 1, 'G': 1}, self.price(session, source))
        before = authoritative_state_hash(session.state)
        bad = session.act('pilot:A', {'action_id': 'cast:' + source.ref,
            'targets': [target.ref], 'pay': {}})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source, [target.ref])
        self.assertEqual((0, 0), (session.state.players['A'].mana_pool['B'], session.state.players['A'].mana_pool['G']))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)

    def test_death_during_real_mana_payment_cannot_retroactively_reduce_locked_cost(self):
        session = self.session(742136)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='hand')
        target = self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='locked-price-target')
        donor = self.add(session.engine, 'Wild Cantor')
        self.prepare(session, mana={'B': 1, 'C': 3})
        self.checkpoint(session)
        self.assertEqual({'GENERIC': 3, 'B': 1, 'G': 1}, self.price(session, source))
        self.cast(session, 'A', source, [target.ref])
        self.assertEqual('graveyard', session.state.cards[donor.object_id].zone)
        self.assertEqual(0, session.state.players['A'].mana_pool['C'])
        self.assertEqual('stack', session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
        self.replay(session, load=True)

    def test_real_bounce_does_not_count_as_a_creature_death(self):
        session = self.session(742137)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='hand')
        self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='bounce-price-target')
        body = self.add(session.engine, 'Grizzly Bears', seat='B', ref='nondeath-bear')
        bounce = self.add(session.engine, 'Boomerang', zone='hand')
        self.prepare(session, mana={'B': 1, 'G': 1, 'U': 2, 'C': 3})
        self.checkpoint(session)
        self.cast(session, 'A', bounce, [body.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[body.object_id].zone)
        self.assertEqual({'GENERIC': 3, 'B': 1, 'G': 1}, self.price(session, source))
        self.replay(session, load=True)

    def test_legacy_disabled_journal_does_not_substitute_the_presentation_log(self):
        session = self.session(742138)
        source = self.add(session.engine, 'Diregraf Rebirth', zone='hand')
        self.add(session.engine, 'Grizzly Bears', zone='graveyard', ref='legacy-price-target')
        body = self.add(session.engine, 'Grizzly Bears', seat='B', ref='legacy-death')
        bolt = self.add(session.engine, 'Lightning Bolt', zone='hand')
        self.prepare(session, mana={'R': 1, 'B': 1, 'G': 1, 'C': 3})
        session.state.turn_history = None
        self.checkpoint(session)
        self.cast(session, 'A', bolt, [body.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[body.object_id].zone)
        self.assertIsNone(session.state.turn_history)
        self.assertEqual({'GENERIC': 3, 'B': 1, 'G': 1}, self.price(session, source))
        self.replay(session, load=True)
