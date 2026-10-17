from __future__ import annotations

"""CR 702.29d: a cycling discard is one occurrence for the combined wording."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/cycle-or-discard-event-cards.json'
CAPABILITY = 'trigger.event.cycle_or_discard'


class CycleOrDiscardCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, registry=None):
        record = CardRecord(oracle_id='fixture:cycle-or-discard', name='Discard Observer',
            mana_cost='{2}{U}', mana_value=3, type_line='Creature — Human', oracle_text=text,
            power='2', toughness='3', loyalty=None, defense=None, colors=('U',), color_identity=('U',),
            keywords=(), produced_mana=(), layout='normal', released_at='2026-01-01',
            legalities={'commander': 'legal'}, faces=(), raw={})
        return compile_oracle_card(record, capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_combined_binding_uses_one_discard_occurrence_and_preserves_another(self):
        from quorune.compiler.cycle_or_discard_bindings import cycle_or_discard_binding_spec
        for subject in ('a', 'another'):
            text = f'Whenever you cycle or discard {subject} card, draw a card.'
            binding = cycle_or_discard_binding_spec(text)
            self.assertEqual('card.discarded', binding.event)
            predicate = {'field': 'previous_controller', 'op': 'eq', 'value': '$source.controller'}
            exclusion = {'field': 'card', 'op': 'ne', 'value': '$source.ref'}
            self.assertEqual({'all': [predicate, exclusion]} if subject == 'another' else predicate, binding.condition)
            ir = self.compile(text)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(text, node.text)
            self.assertEqual((0, len(text)), (node.span.start, node.span.end))
            self.assertIn(CAPABILITY, node.capability_dependencies)
        for result in ('this creature gets +1/+1 until end of turn.',
                'this creature gains indestructible until end of turn.'):
            ir = self.compile('Whenever you cycle or discard a card, ' + result)
            self.assertEqual('exact', ir.status, ir.material_residuals)
            self.assertEqual('$source.zone_object', ir.faces[0].nodes[0].effects[0]['card'])

    def test_unknown_unions_results_and_missing_dependencies_remain_residual(self):
        for text in ('Whenever you cycle or discard a creature card, draw a card.',
                'Whenever a player cycles or discards a card, draw a card.',
                'Whenever you cycle or exile a card, draw a card.',
                'Whenever you cycle and discard a card, draw a card.',
                'Whenever you cycle or discard a card, rewrite history.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(text).status)
        text = 'Whenever you cycle or discard a card, draw a card.'
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in (CAPABILITY, 'trigger.event.normalized_zone_change', 'trigger.placement.apnap'):
            changed = json.loads(json.dumps(raw))
            next(r for r in changed['capabilities'] if r['id'] == capability).update(status='blocked', blockers=['owner omitted'])
            self.assertNotEqual('exact', self.compile(text, CapabilityRegistry(changed)).status)


class CycleOrDiscardRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.SpreeModeCostRuntimeTests.session
    add = actions.SpreeModeCostRuntimeTests.add
    checkpoint = actions.SpreeModeCostRuntimeTests.checkpoint
    resolve = actions.SpreeModeCostRuntimeTests.resolve
    replay = actions.SpreeModeCostRuntimeTests.replay
    prepare = actions.SpreeModeCostRuntimeTests.prepare
    assert_closed = actions.SpreeModeCostRuntimeTests.assert_closed
    reject = actions.SpreeModeCostRuntimeTests.reject
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'cycle-discard.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Cycle/discard witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def finish(self, session):
        for _ in range(80):
            decision = self.resolve(session)
            if decision is None:
                return
            self.assertEqual('trigger.order', decision.kind)
            principal = session.pending_principals()[0]
            packet = session.packet(principal, full=True)['decision']
            result = session.act(principal,
                {'action_id': 'order', 'triggers': [row['id'] for row in packet['ctx']['triggers']]})
            self.assertTrue(result.ok, result.summary)
        self.fail('Combined discard triggers did not finish')

    @staticmethod
    def stats(engine, card):
        return engine._numeric_stat(card.object_id, 'power'), engine._numeric_stat(card.object_id, 'toughness')

    def test_actual_cycling_grows_only_the_controller_observer_once_before_private_draw(self):
        session = self.session(702029401)
        engine = session.engine
        observer = self.add(engine, 'Hekma Sentinels', ref='cycle-discard-own')
        other = self.add(engine, 'Hekma Sentinels', seat='B', ref='cycle-discard-other')
        source = self.add(engine, 'Curator of Mysteries', zone='hand', ref='cycle-discard-card')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='cycle-discard-private')
        self.assert_closed(session, observer)
        self.assert_closed(session, source)
        self.prepare(session, mana={'U': 1})
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:' + source.ref + ':'))
        draws = {seat: len(session.state.players[seat].draw_history) for seat in 'ABCD'}
        self.checkpoint(session)
        self.reject(session, offered['id'], 'D')
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', source.zone)
        self.assertEqual(draws['A'], len(session.state.players['A'].draw_history))
        self.assertEqual(2, len(session.state.stack))
        self.replay(session, load=True)
        self.finish(session)
        self.assertEqual((3, 4), self.stats(engine, observer))
        self.assertEqual((2, 3), self.stats(engine, other))
        self.assertEqual(draws['A'] + 1, len(session.state.players['A'].draw_history))
        for seat in 'BCD':
            self.assertEqual(draws[seat], len(session.state.players[seat].draw_history))
        self.replay(session)

    def test_actual_whole_hand_discard_triggers_per_card_and_keeps_other_seats_separate(self):
        session = self.session(702029402)
        engine = session.engine
        observer = self.add(engine, 'Hekma Sentinels', ref='discard-own')
        other = self.add(engine, 'Hekma Sentinels', seat='B', ref='discard-other')
        spell = self.add(engine, 'One with Nothing', zone='hand', ref='discard-whole-hand')
        self.add(engine, 'Generic Bound Growth', zone='hand', ref='discard-extra')
        self.assert_closed(session, observer)
        self.assert_closed(session, spell)
        self.prepare(session, mana={'B': 1})
        count = len(session.state.players['A'].zones['hand']) - 1
        self.assertGreater(count, 1)
        self.checkpoint(session)
        self.reject(session, 'cast:' + spell.ref, 'C', pay='auto')
        result = session.act('pilot:A', {'action_id': 'cast:' + spell.ref, 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.finish(session)
        self.assertEqual([], session.state.players['A'].zones['hand'])
        self.assertEqual((2 + count, 3 + count), self.stats(engine, observer))
        self.assertEqual((2, 3), self.stats(engine, other))
        self.replay(session, load=True)

    def test_controller_predicate_mutation_keeps_admission_but_changes_native_consequences(self):
        from quorune.compiler import cycle_or_discard_bindings as bindings
        original = bindings.cycle_or_discard_binding_spec

        def inverted(text):
            spec = original(text)
            if spec is None:
                return None
            return replace(spec, condition={'field': 'previous_controller', 'op': 'ne', 'value': '$source.controller'})

        witness = type(self)('test_actual_cycling_grows_only_the_controller_observer_once_before_private_draw')
        result = unittest.TestResult()
        with mock.patch.object(bindings, 'cycle_or_discard_binding_spec', inverted):
            witness.run(result)
        self.assertEqual(1, result.testsRun)
        self.assertEqual([], result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn('(3, 4)', result.failures[0][1])

    def test_actual_ability_removal_and_departure_keep_discovery_and_locked_trigger_distinct(self):
        for removed_before_cycle in (False, True):
            with self.subTest(removed_before_cycle=removed_before_cycle):
                session = self.session(702029403 + removed_before_cycle)
                engine = session.engine
                observer = self.add(engine, 'Hekma Sentinels', ref='temporal-discard-observer')
                source = self.add(engine, 'Curator of Mysteries', zone='hand', ref='temporal-cycle-card')
                response = self.add(engine, 'Humble' if removed_before_cycle else 'Unsummon',
                    zone='hand', ref='temporal-discard-response')
                self.assert_closed(session, observer)
                self.prepare(session, mana={'C': 1, 'W': 1, 'U': 2})
                self.checkpoint(session)
                if removed_before_cycle:
                    self.cast(session, 'A', response, [observer.ref])
                    self.finish(session)
                    self.assertEqual((0, 1), self.stats(engine, observer))
                action = 'activate:' + source.ref + ':ab3'
                self.assertIn(action, [row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']])
                result = session.act('pilot:A', {'action_id': action, 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(1 if removed_before_cycle else 2, len(session.state.stack))
                if not removed_before_cycle:
                    self.cast(session, 'A', response, [observer.ref])
                self.replay(session, load=True)
                self.finish(session)
                self.assertEqual('battlefield' if removed_before_cycle else 'hand', observer.zone)
                self.assertEqual((0, 1) if removed_before_cycle else (2, 3), self.stats(engine, observer))
                self.assertEqual('graveyard', source.zone)
                self.replay(session)

    def test_actual_replaced_cycling_discard_still_triggers_once(self):
        session = self.session(702029405)
        engine = session.engine
        observer = self.add(engine, 'Hekma Sentinels', ref='replaced-discard-observer')
        source = self.add(engine, 'Curator of Mysteries', zone='hand', ref='replaced-cycle-card')
        self.add(engine, 'Dauthi Voidwalker', seat='B', ref='discard-exile-replacement')
        self.assert_closed(session, observer)
        self.prepare(session, mana={'U': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'activate:' + source.ref + ':ab3', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('exile', source.zone)
        self.assertEqual(1, source.counters.get('void'))
        self.assertEqual(2, len(session.state.stack))
        self.replay(session, load=True)
        self.finish(session)
        self.assertEqual((3, 4), self.stats(engine, observer))
        self.replay(session)

    def test_actual_archfiend_cycles_one_discard_into_one_counter_for_each_opponent_creature(self):
        session = self.session(702029406)
        engine = session.engine
        observer = self.add(engine, 'Archfiend of Ifnir', ref='discard-counter-observer')
        source = self.add(engine, 'Curator of Mysteries', zone='hand', ref='discard-counter-cycle')
        others = [self.add(engine, 'Hekma Sentinels', seat=seat, ref='discard-counter-' + seat) for seat in 'BCD']
        self.assert_closed(session, observer)
        self.prepare(session, mana={'U': 1})
        self.checkpoint(session)
        result = session.act('pilot:A', {'action_id': 'activate:' + source.ref + ':ab3', 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, len(session.state.stack))
        self.finish(session)
        self.assertEqual(0, observer.counters.get('-1/-1', 0))
        for card in others:
            self.assertEqual(1, card.counters.get('-1/-1'))
            self.assertEqual((1, 2), self.stats(engine, card))
        self.replay(session, load=True)

    def test_actual_faith_payment_resolves_once_and_retains_private_pending_choice(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                session = self.session(702029407 + pay)
                engine = session.engine
                observer = self.add(engine, 'Faith of the Devoted', ref='discard-payment-observer')
                source = self.add(engine, 'Curator of Mysteries', zone='hand', ref='discard-payment-cycle')
                self.assert_closed(session, observer)
                self.prepare(session, mana={'C': 1, 'U': 1})
                life = {seat: session.state.players[seat].life for seat in 'ABCD'}
                self.checkpoint(session)
                result = session.act('pilot:A', {'action_id': 'activate:' + source.ref + ':ab3', 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(2, len(session.state.stack))
                pending = self.resolve(session)
                self.assertIsNotNone(pending)
                self.assertEqual(['A'], pending.actors)
                for seat in 'BCD':
                    self.assertIsNone(session.packet('pilot:' + seat, full=True)['decision'])
                session = self.replay(session, load=True)
                self.reject(session, 'choose', 'B', pay=True)
                result = session.act('pilot:A', {'action_id': 'choose', 'pay': pay})
                self.assertTrue(result.ok, result.summary)
                self.finish(session)
                self.assertEqual(life['A'] + (2 if pay else 0), session.state.players['A'].life)
                for seat in 'BCD':
                    self.assertEqual(life[seat] - (2 if pay else 0), session.state.players[seat].life)
                self.assertEqual(0 if pay else 1, session.state.players['A'].mana_pool.get('C', 0))
                self.replay(session)

    def test_original_cards_require_complete_results_and_normal_registration(self):
        for raw in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']:
            if 'cycle or discard' not in raw['oracle_text'].casefold():
                continue
            with self.subTest(card=raw['name']):
                record = self.db.lookup(raw['name'])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                expected = record.name != 'Cunning Survivor'
                self.assertEqual(expected, bound['strict_capability_ready'], program.residuals)
                semantics = SemanticRegistry()
                register_generated_programs(self.db, semantics, (record,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                if expected:
                    programs = tuple(p for p in semantics.programs_for_oracle(record.oracle_id)
                        if 'generated_oracle_ir' in p.coverage)
                    self.assertTrue(programs)
                    self.assertTrue(all(p.trust_level == 'trusted' for p in programs))
