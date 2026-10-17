from __future__ import annotations

"""Pinned CR 702.56: repeated additional payments and one copy trigger."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.replicate_model import FixedManaReplicateSpec, REPLICATE_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.util import mana_cost_to_vector
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_spree_mode_costs as actions
import test_self_public_zone_casting as zone_actions


FIXTURE = ROOT / 'tests/fixtures/fixed-replicate-cards.json'


class FixedReplicateModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def record(self, cost='Replicate {1}{U}', body='Draw a card.', **changes):
        return replace(CardRecord(oracle_id='fixture:replicate-boundary', name='Replicate Witness',
            mana_cost='{1}{U}', mana_value=2, type_line='Sorcery', oracle_text=cost + '\n' + body,
            power=None, toughness=None, loyalty=None, defense=None, colors=('U',), color_identity=('U',),
            keywords=('Replicate',), produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={}), **changes)

    def compile(self, record=None, registry=None):
        return compile_oracle_card(record or self.record(), capability_registry=registry or self.registry,
            capability_profile='commander_review')

    def test_replicate_spec_is_immutable_and_payment_count_is_instance_correlated(self):
        vector = mana_cost_to_vector('{1}{U}')[0]
        spec = FixedManaReplicateSpec('front:replicate:line:1', vector)
        vector['U'] = 99
        self.assertEqual(1, spec.requirements['U'])
        self.assertEqual(spec, FixedManaReplicateSpec.from_dict(spec.to_dict()))
        self.assertEqual(3, spec.payment_fact(3)['count'])
        for change in ({'schema_version': True}, {'extra': 1}, {'requirements': {'U': 1}},
                {'requirements': mana_cost_to_vector('{0}')[0]},
                {'requirements': {**dict(spec.requirements), 'U': True}}):
            with self.assertRaises(ValueError):
                FixedManaReplicateSpec.from_dict({**spec.to_dict(), **change})
        for count in (0, -1, True, 1.5, '2'):
            with self.assertRaises(ValueError):
                spec.payment_fact(count)

    def test_replicate_source_span_and_paired_dependency_boundary(self):
        from quorune.semantic_runtime.replicate import FixedManaReplicateHandler
        ir = self.compile()
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual('Replicate {1}{U}', node.text)
        self.assertEqual((0, len(node.text)), (node.span.start, node.span.end))
        self.assertIn(REPLICATE_CAPABILITY, node.capability_dependencies)
        handler = FixedManaReplicateHandler()
        self.assertEqual(1, handler.validate(node.handlers[0]).requirements['U'])
        for field, value in [('schema_version', True), ('requires_complete_card_program', False), ('extra', 1)]:
            with self.assertRaises(SemanticNodeError):
                handler.validate({**node.handlers[0], field: value})
        for cost in ('Replicate {X}', 'Replicate {U/R}', 'Replicate {0}', 'Replicate—Pay {E}{E}{E}.',
                'Replicate {1}\nReplicate {U}'):
            self.assertNotEqual('exact', self.compile(self.record(cost=cost)).status)
        self.assertNotEqual('exact', self.compile(self.record(body='Invent an unrepresented sibling.')).status)
        self.assertNotEqual('exact', self.compile(self.record(type_line='Creature — Wizard', power='1', toughness='1')).status)
        value = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in (REPLICATE_CAPABILITY, 'target.revalidate_resolution', 'trigger.placement.apnap'):
            changed = deepcopy(value)
            next(row for row in changed['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['Replicate dependency withdrawn'])
            self.assertNotEqual('exact', self.compile(registry=CapabilityRegistry(changed)).status)

    def test_replicate_producer_omission_is_detected_by_independent_positive_contract(self):
        with mock.patch('quorune.compiler.replicate_nodes.printed_replicate_spec', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_replicate_source_span_and_paired_dependency_boundary()


class FixedReplicateRuntimeTests(unittest.TestCase):
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
        path = Path(cls.temp.name) / 'replicate.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json', FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Replicate witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def replicated_cast(self, session, source, count, targets=(), *, cost_base=None, **payment):
        for _ in range(16):
            if session.state.priority_player == 'A':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        current = session.state.cards[source.object_id]
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + current.ref)
        option = next(row['id'] for row in offered['cost_options'] if '+replicate:' in row['id']
            and (cost_base is None or row['id'].startswith(cost_base + '+replicate:')))
        result = session.act('pilot:A', {'action_id': offered['id'], 'cost_option': option,
            'replicate_count': count, 'targets': list(targets), 'pay': 'auto', **payment})
        self.assertTrue(result.ok, result.summary)

    def test_actual_pyromatics_pays_repeated_total_cost_and_retargets_independent_copies(self):
        session = self.session(70256001)
        engine = session.engine
        source = self.add(engine, 'Pyromatics', zone='hand', ref='replicate-pyromatics')
        self.add(engine, 'Sphere of Resistance', ref='replicate-increase')
        self.add(engine, 'Ruby Medallion', ref='replicate-reduction')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='replicate-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 3, 'R': 3})
        before = {seat: engine.state.players[seat].life for seat in 'BCD'}
        self.checkpoint(session)
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:B', full=True)))
        self.reject(session, 'cast:' + source.ref, 'D', replicate_count=2, targets=['D'], pay='auto')
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + source.ref)
        paid_option = next(row['id'] for row in offered['cost_options'] if '+replicate:' in row['id'])
        self.reject(session, 'cast:' + source.ref, cost_option=paid_option, replicate_count=2,
            targets=['D'], pay='manual', payment={})
        self.replicated_cast(session, source, 2, ['D'])
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(2, len(session.state.stack))
        pending = self.resolve(session)
        self.assertEqual('semantic.storm', pending.kind)
        self.reject(session, 'choose', 'B', copy_targets=[['B'], ['C']])
        self.reject(session, 'choose', copy_targets=[['B']])
        result = session.act('pilot:A', {'action_id': 'choose', 'copy_targets': [['B'], ['C']]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, sum(item.kind == 'spell_copy' for item in session.state.stack))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual({seat: value - 1 for seat, value in before.items()},
            {seat: session.state.players[seat].life for seat in 'BCD'})
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_countered_original_retains_paid_copy_trigger_without_recursive_casts(self):
        session = self.session(70256002)
        engine = session.engine
        source = self.add(engine, 'Pyromatics', zone='hand', ref='replicate-countered-original')
        counter = self.add(engine, 'Counterspell', seat='B', zone='hand', ref='replicate-original-counter')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 2
        self.prepare(session, mana={'C': 3, 'R': 3})
        before = {seat: engine.state.players[seat].life for seat in 'BCD'}
        self.checkpoint(session)
        self.replicated_cast(session, source, 2, ['D'])
        original = next(item for item in session.state.stack if item.kind == 'spell')
        self.cast(session, 'B', counter, [original.ref])
        session = self.replay(session, load=True)
        pending = self.resolve(session)
        self.assertEqual('semantic.storm', pending.kind)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(session.state.stack))
        result = session.act('pilot:A', {'action_id': 'choose', 'copy_targets': [['B'], ['C']]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, len(session.state.stack))
        self.assertTrue(all(item.kind == 'spell_copy' for item in session.state.stack))
        self.resolve(session)
        self.assertEqual(before['B'] - 1, session.state.players['B'].life)
        self.assertEqual(before['C'] - 1, session.state.players['C'].life)
        self.assertEqual(before['D'], session.state.players['D'].life)
        self.assertEqual(2, len(session.engine._current_turn_history('spell_cast')))
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_free_base_retains_optional_replicate_cost_and_zero_creates_no_trigger(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant, ImpulseAccessDuration
        for count in (0, 2):
            with self.subTest(count=count):
                session = self.session(70256003 + count)
                engine = session.engine
                source = self.add(engine, 'Train of Thought', zone='hand', ref='replicate-free-draw')
                self.assert_closed(session, source)
                grant_temporary_cast_permission(engine, card=source,
                    grant=TemporaryCastPermissionGrant(player='A', duration=ImpulseAccessDuration.END_OF_TURN,
                        not_before_turn_sequence=None, without_mana_cost=True, source='replicate-free-base-contract'),
                    reason='Free base retains independently optional additional Replicate payments')
                self.prepare(session, mana={'C': count, 'U': count})
                drawn = [session.state.cards[oid].ref for oid in session.state.players['A'].zones['library'][-(count + 1):]]
                hand_before = len(session.state.players['A'].zones['hand'])
                self.checkpoint(session)
                if count:
                    for invalid in (-1, True, '2', 1.5):
                        self.reject(session, 'cast:' + source.ref, replicate_count=invalid, pay='auto')
                    self.reject(session, 'cast:' + source.ref, replicate_count=1, cost_option='without_mana_cost', pay='auto')
                    self.replicated_cast(session, source, count, cost_base='without_mana_cost')
                else:
                    result = session.act('pilot:A', {'action_id': 'cast:' + source.ref,
                        'cost_option': 'without_mana_cost', 'replicate_count': 0, 'pay': 'auto'})
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual(1, len(session.state.stack))
                self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
                session = self.replay(session, load=True)
                self.resolve(session)
                self.assertEqual(hand_before + count, len(session.state.players['A'].zones['hand']))
                hand_refs = {session.state.cards[oid].ref for oid in session.state.players['A'].zones['hand']}
                self.assertTrue(set(drawn).issubset(hand_refs))
                for seat in 'BCD':
                    packet = json.dumps(session.packet('pilot:' + seat, full=True))
                    for ref in drawn:
                        self.assertNotIn(ref, packet)
                self.assertFalse(session.state.stack)
                self.replay(session)

    def test_replicate_copies_revalidate_departed_targets_and_reject_illegal_new_targets(self):
        session = self.session(70256006)
        engine = session.engine
        source = self.add(engine, 'Gigadrowse', zone='hand', ref='replicate-tap')
        first = self.add(engine, 'Generic Bound Body', seat='B', ref='replicate-stale-target')
        second = self.add(engine, 'Generic Bound Body', seat='C', ref='replicate-live-target')
        bounce = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='replicate-stale-bounce')
        self.assert_closed(session, source)
        engine.state.players['B'].mana_pool['U'] = 1
        self.prepare(session, mana={'U': 3})
        self.checkpoint(session)
        self.replicated_cast(session, source, 2, [first.ref])
        pending = self.resolve(session)
        self.assertEqual('semantic.storm', pending.kind)
        self.reject(session, 'choose', copy_targets=[['D'], [second.ref]])
        result = session.act('pilot:A', {'action_id': 'choose', 'copy_targets': [[first.ref], [second.ref]]})
        self.assertTrue(result.ok, result.summary)
        self.cast(session, 'B', bounce, [first.ref])
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[first.object_id].zone)
        self.assertTrue(session.state.cards[second.object_id].tapped)
        self.assertFalse(session.state.cards[first.object_id].tapped)
        self.assertFalse(session.state.stack)
        self.replay(session)

    def test_original_replicate_cards_require_complete_normal_admission(self):
        expected = {'Lose Focus', 'Super Combo', 'Train of Thought', 'Pyromatics', 'Gigadrowse',
            'Leap of Flame', 'Vacuumelt', 'Shattering Spree'}
        rows = [r for r in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']
            if 'Replicate' in r.get('keywords', ())]
        self.assertEqual(19, len(rows))
        for row in rows:
            with self.subTest(name=row['name']):
                record = self.db.by_oracle_id(row['oracle_id'])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(row['name'] in expected, binding['strict_capability_ready'], binding)
                if row['name'] in expected:
                    session = self.session(70256007)
                    source = self.add(session.engine, row['name'], zone='hand', ref='replicate-original-table')
                    self.assert_closed(session, source)

    def test_replicate_repeat_price_omission_preserves_admission_but_changes_native_payment(self):
        from quorune.rules.casting import replicate_costs as owner
        original = owner.expand_repeated_mana_costs
        changed_prices = []
        def omitted(options, schema, **kwargs):
            result = original(options, schema, **kwargs)
            if kwargs['suffix_name'] == 'replicate':
                for option in result:
                    fact = option.get('replicate_paid')
                    if fact is not None:
                        changed_prices.append((dict(option['requirements']), fact['count']))
                        for symbol, amount in schema['replicate_paid']['requirements'].items():
                            option['requirements'][symbol] -= amount * fact['count']
            return result
        result = unittest.TestResult()
        witness = type(self)('test_actual_pyromatics_pays_repeated_total_cost_and_retargets_independent_copies')
        with mock.patch.object(owner, 'expand_repeated_mana_costs', omitted):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertTrue(changed_prices)
        self.assertEqual(1, len(result.failures))
        self.assertIn("sum(session.state.players['A'].mana_pool.values())", result.failures[0][1])
