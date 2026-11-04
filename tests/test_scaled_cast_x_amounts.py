from __future__ import annotations

"""Bounded scalar multiples of announced X: CR 107.3, 608.2c, 707.10."""

import unittest
from types import SimpleNamespace

from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import CastXAmountSpec, PublicQueryAmountError
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.query_effect_amounts import resolve_cast_x_amount
from test_declared_effect_amounts import generic_spell


class ScaledCastXCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def test_scaled_results_use_one_announced_x_without_changing_fixed_siblings(self):
        for text, coefficients in (
            ('You gain twice X life.', [2]),
            ('You gain three times X life.', [3]),
            ('Each opponent loses two times X life. You gain life equal to the life lost this way.', [2]),
            ('You gain twice X life and draw X cards.', [2, 1]),
            ('Target creature gets -X/-X until end of turn. You gain twice X life.', [-1, -1, 2]),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(generic_spell(text), capability_registry=self.registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                def amounts(value):
                    if isinstance(value, dict):
                        if value.get('kind') == 'cast_x_effect_amount':
                            return [value['coefficient']]
                        return [n for child in value.values() for n in amounts(child)]
                    if isinstance(value, (tuple, list)):
                        return [n for child in value for n in amounts(child)]
                    return []
                self.assertEqual(coefficients, amounts(ir.faces[0].nodes[0].effects))

    def test_unowned_arithmetic_target_counts_and_public_definitions_stay_residual(self):
        for text in (
            'Draw X plus two cards.',
            'You gain four times X life.',
            'Destroy twice X target creatures.',
            'You gain twice X life. Rewrite history.',
            'You gain twice X life, where X is the number of creatures you control.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(
                    generic_spell(text), capability_registry=self.registry).status)

    def test_versions_keep_legacy_encoding_and_reject_malformed_coefficients(self):
        old = {'kind': 'cast_x_effect_amount', 'schema_version': 1, 'coefficient': 1}
        self.assertEqual(old, CastXAmountSpec().to_dict())
        self.assertEqual(old, CastXAmountSpec.from_dict(old).to_dict())
        for coefficient in (-3, -2, 2, 3):
            spec = CastXAmountSpec(coefficient=coefficient, schema_version=2)
            self.assertEqual(spec, CastXAmountSpec.from_dict(spec.to_dict()))
            for x in (0, 3):
                self.assertEqual(coefficient*x, resolve_cast_x_amount(spec.to_dict(), SimpleNamespace(x_value=x)))
        for version, coefficient in ((1, 2), (2, 1), (2, 0), (2, 4), (2, True), (2, 2.0), (True, 2), (3, 2)):
            with self.subTest(version=version, coefficient=coefficient), self.assertRaises(PublicQueryAmountError):
                CastXAmountSpec.from_dict({**old, 'schema_version': version, 'coefficient': coefficient})
        with self.assertRaises(PublicQueryAmountError):
            CastXAmountSpec.from_dict({**old, 'binding_id': 'x'})

    def test_scaled_owner_withdrawal_and_stripped_dependency_fail_closed(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY, cast_x_runtime_dependencies
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(c for c in raw['capabilities'] if c['id'] == DECLARED_AMOUNT_CAPABILITY).update(
            status='blocked', blockers=['Declared amount authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual('exact', compile_oracle_card(generic_spell('You gain twice X life.'),
            capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(generic_spell('You gain 6 life.'),
            capability_registry=registry).status)
        effect = {'op': 'life', 'delta': CastXAmountSpec(coefficient=2, schema_version=2).to_dict()}
        self.assertEqual((DECLARED_AMOUNT_CAPABILITY,), cast_x_runtime_dependencies(effect))

    def test_scaled_multiplication_omission_mutant_is_killed(self):
        import inspect
        import quorune.semantic_runtime.query_effect_amounts as owner
        source = inspect.getsource(owner.resolve_cast_x_amount)
        self.assertEqual(1, source.count('return spec.coefficient * amount'))
        namespace = dict(vars(owner))
        exec(compile(source.replace('return spec.coefficient * amount', 'return amount'),
            '<omitted-scaled-x-multiplication>', 'exec'), namespace)
        spec = CastXAmountSpec(coefficient=2, schema_version=2).to_dict()
        with self.assertRaises(AssertionError):
            self.assertEqual(6, namespace['resolve_cast_x_amount'](spec, SimpleNamespace(x_value=3)))


import json
from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class ScaledCastXNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)/'scaled-cast-x.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/scaled-cast-x-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Scaled X', [DeckEntry('Isamaru, Hound of Konda', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 14)], ['Isamaru, Hound of Konda'])

    def reject(self, s, seat, command):
        before = authoritative_state_hash(s.state)
        result = s.act('pilot:'+seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state))

    def cast_x(self, s, spell, x, targets=()):
        self.priority(s, 'A')
        command = {'action_id': 'cast:'+spell.ref, 'x': x, 'targets': list(targets), 'pay': 'auto'}
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_debt_zero_and_positive_x_use_original_cost_and_aggregate_loss(self):
        for x in (0, 3):
            with self.subTest(x=x):
                s = self.session(119700610+x)
                spell = self.add(s.engine, 'Debt to the Deathless', zone='hand')
                pride = self.add(s.engine, "Ajani's Pridemate")
                private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
                self.assert_closed(s, spell)
                self.prepare(s, mana={'W': 2, 'B': 2, 'C': 3})
                self.checkpoint(s)
                self.reject(s, 'A', {'action_id': 'cast:'+spell.ref, 'x': -1, 'pay': 'auto'})
                self.cast_x(s, spell, x)
                self.assertEqual(3-x, sum(s.state.players['A'].mana_pool.values()))
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual((40+6*x, 40-2*x, 40-2*x, 40-2*x),
                    tuple(s.state.players[p].life for p in 'ABCD'))
                self.assertEqual(int(x>0), s.state.cards[pride.object_id].counters.get('+1/+1', 0))
                for viewer in 'ABD':
                    self.assertNotIn(private.ref, json.dumps(s.packet('pilot:'+viewer, full=True)))
                self.replay(s, load=True)

    def test_actual_copied_debt_keeps_x_and_uses_its_own_controller(self):
        s = self.session(119700620)
        spell = self.add(s.engine, 'Debt to the Deathless', zone='hand')
        copy = self.add(s.engine, 'Reverberate', seat='B', zone='hand')
        s.state.players['B'].mana_pool.update({'R': 2})
        self.assert_closed(s, spell)
        self.assert_closed(s, copy)
        self.prepare(s, mana={'W': 2, 'B': 2, 'C': 3})
        self.checkpoint(s)
        self.cast_x(s, spell, 3)
        self.cast(s, 'B', copy, [s.state.stack[-1].ref])
        self.resolve_top(s)
        self.assertEqual(('spell_copy', 3, 'B'),
            (s.state.stack[-1].kind, s.state.stack[-1].x_value, s.state.stack[-1].controller))
        s = self.replay(s, load=True)
        self.resolve_top(s)
        self.assertEqual((34, 58, 34, 34), tuple(s.state.players[p].life for p in 'ABCD'))
        self.assertIsNone(self.resolve(s))
        self.assertEqual((52, 52, 28, 28), tuple(s.state.players[p].life for p in 'ABCD'))
        self.replay(s, load=True)

    def test_actual_procrastinate_keeps_one_target_and_places_scaled_stun_after_tap(self):
        for x in (0, 3):
            with self.subTest(x=x):
                s = self.session(119700630+x)
                spell = self.add(s.engine, 'Procrastinate', zone='hand')
                target = self.add(s.engine, 'Stone Golem', seat='B')
                self.assert_closed(s, spell)
                self.prepare(s, mana={'U': 1, 'C': 3})
                self.checkpoint(s)
                self.reject(s, 'A', {'action_id': 'cast:'+spell.ref, 'x': x, 'targets': [], 'pay': 'auto'})
                self.cast_x(s, spell, x, [target.ref])
                self.assertEqual(3-x, sum(s.state.players['A'].mana_pool.values()))
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                current = s.state.cards[target.object_id]
                self.assertTrue(current.tapped)
                self.assertEqual(2*x, current.counters.get('stun', 0))
                self.replay(s, load=True)
