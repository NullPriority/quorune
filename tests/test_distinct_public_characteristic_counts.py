"""Distinct public characteristic values across applied effects and costs."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.characteristic_evaluation import type_parts
from quorune.compiler.public_characteristic_aggregates import public_characteristic_aggregate
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card
from quorune.public_characteristic_aggregate_model import (
    DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY,
    PublicCharacteristicAggregateSpec,
)
from quorune.public_characteristic_aggregates import current_public_characteristic_aggregate
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_fixed_copy_type_exceptions as copy_controls


class DistinctPublicCharacteristicCountTests(unittest.TestCase):
    def test_distinct_count_preserves_signed_membership_and_kills_duplicate_mutant(self):
        def body(ref, owner='A', phased=False):
            return CardInstance(object_id=ref, ref=ref, oracle_id='control', printed_name='Creature',
                                owner=owner, controller=owner, zone='battlefield', phased_out=phased)

        cards = [body('first'), body('duplicate'), body('positive'), body('foreign', owner='B'),
                 body('phased', phased=True)]
        power = {'first': -1, 'duplicate': -1, 'positive': 3, 'foreign': 8, 'phased': 9}
        host = SimpleNamespace(
            state=SimpleNamespace(cards={card.object_id: card for card in cards}, players={'A': None, 'B': None}),
            _type_parts=type_parts,
            _effective_card_data=lambda card: {'type_line': 'Creature — Bear', 'power': power[card.ref], 'toughness': 3},
        )
        distinct = public_characteristic_aggregate('different power among creatures you control').aggregate
        maximum = public_characteristic_aggregate('greatest power among creatures you control').aggregate
        self.assertEqual(2, current_public_characteristic_aggregate(host, 'A', distinct))
        self.assertEqual(3, current_public_characteristic_aggregate(host, 'A', maximum))
        with patch('quorune.public_characteristic_aggregates.set', list, create=True):
            with self.assertRaises(AssertionError):
                self.assertEqual(2, current_public_characteristic_aggregate(host, 'A', distinct))
        self.assertEqual(distinct, PublicCharacteristicAggregateSpec.from_dict(distinct.to_dict()))
        for change in ({'schema_version': 1}, {'schema_version': True}, {'controller_relation': 'any'},
                       {'reduction': 'sum'}, {'query': {**distinct.query.to_dict(), 'zones': ['hand']}}):
            with self.assertRaises(ValueError):
                PublicCharacteristicAggregateSpec.from_dict({**distinct.to_dict(), **change})

    def test_distinct_public_count_compiler_requires_scope_and_capability(self):
        registry = load_default_capability_registry()
        text = 'Draw a card for each different mana value among nonland cards in your graveyard.'
        ir = compile_oracle_card(record(text, 'Sorcery'), capability_registry=registry,
                                 capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertIn(DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY, node.capability_dependencies)
        for suffix in ('cards in your hand', 'cards in your library', 'cards exiled with this permanent'):
            hidden = 'Draw a card for each different mana value among ' + suffix + '.'
            self.assertNotEqual('exact', compile_oracle_card(record(hidden, 'Sorcery'),
                capability_registry=registry, capability_profile='commander_review').status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        capability = next(row for row in raw['capabilities'] if row['id'] == DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY)
        capability.update(status='blocked', blockers=['Distinct public count capability mutation'])
        altered = CapabilityRegistry(raw)
        altered.mark_evidence_verified(registry.evidence_fingerprint)
        blocked = compile_oracle_card(record(text, 'Sorcery'), capability_registry=altered,
                                      capability_profile='commander_review')
        self.assertNotEqual('exact', blocked.status)
        self.assertTrue(any(DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY in blocker
                            for residual in blocked.material_residuals for blocker in residual.blockers))


class DistinctPublicCharacteristicCountActionTests(InheritedNativeFixture, unittest.TestCase):
    choose_copy = copy_controls.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / 'distinct-public-values.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/creature-power-multiple-cards.json',
            ROOT / 'tests/fixtures/state-based-destination-cards.json',
            ROOT / 'tests/fixtures/distinct-public-value-cards.json',
            ROOT / 'tests/fixtures/fixed-copy-type-exception-cards.json',
            ROOT / 'tests/fixtures/static-characteristic-setting-cards.json',
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Distinct public values', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20),
        ], ['Generic Bound Commander'])

    def test_original_golden_ratio_counts_distinct_signed_power_after_response(self):
        session = self.session(608_208_271)
        spell = self.add(session.engine, 'Golden Ratio', zone='hand')
        wall = self.add(session.engine, 'Wall of Stone')
        self.add(session.engine, 'Ornithopter')
        self.add(session.engine, 'Grizzly Bears', ref='ratio-bear-first')
        self.add(session.engine, 'Grizzly Bears', ref='ratio-bear-second')
        self.add(session.engine, 'Serra Angel')
        self.add(session.engine, 'Hill Giant', seat='C')
        response = self.add(session.engine, 'Disfigure', seat='B', zone='hand')
        self.assert_closed(session, spell)
        self.assert_closed(session, response)
        self.prepare(session, mana={'C': 1, 'G': 1, 'U': 1})
        session.state.players['B'].mana_pool['B'] = 1
        self.checkpoint(session)
        before = len(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', spell)
        self.cast_for(session, 'B', response, [wall.ref])
        self.resolve_top(session)
        self.assertEqual(-2, session.engine._numeric_stat(wall.object_id, 'power'))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before - 1 + 4, len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_original_lunar_insight_counts_copied_values_and_nonland_zero(self):
        session = self.session(202_003_272)
        spell = self.add(session.engine, 'Lunar Insight', zone='hand')
        clone = self.add(session.engine, 'Clone', zone='hand')
        bear = self.add(session.engine, 'Grizzly Bears')
        self.add(session.engine, 'Serra Angel')
        self.add(session.engine, 'Ornithopter')
        self.add(session.engine, 'Forest')
        self.add(session.engine, 'Hill Giant', seat='C')
        for card in (spell, clone):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 5, 'U': 2})
        self.checkpoint(session)
        self.cast(session, 'A', clone)
        session = self.choose_copy(session, bear)
        copied = session.state.cards[clone.object_id]
        self.assertEqual(2, session.engine._effective_card_data(copied)['mana_value'])
        before = len(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', spell)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before - 1 + 3, len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_original_sudden_insight_counts_front_split_and_zero_values_in_own_graveyard(self):
        session = self.session(712_008_272)
        spell = self.add(session.engine, 'Sudden Insight', zone='hand')
        for name in ('Bala Ged Recovery // Bala Ged Sanctuary', 'Mouth // Feed', 'Giant Growth',
                     'Unsummon', 'Ornithopter', 'Forest'):
            self.add(session.engine, name, zone='graveyard')
        self.add(session.engine, 'Grizzly Bears', seat='C', zone='graveyard')
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 4, 'U': 2})
        self.checkpoint(session)
        before = set(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', spell)
        self.assertIsNone(self.resolve(session))
        drawn = set(session.state.players['A'].zones['hand']) - before
        self.assertEqual(4, len(drawn))
        for viewer in 'BCD':
            packet = json.dumps(session.packet('pilot:' + viewer, full=True))
            for oid in drawn:
                self.assertNotIn(session.state.cards[oid].ref, packet)
        self.replay(session, load=True)

    def test_original_eris_cast_pays_for_distinct_instant_and_sorcery_values(self):
        session = self.session(601_002_272)
        spell = self.add(session.engine, 'Eris, Roar of the Storm', zone='hand')
        for name in ('Bala Ged Recovery // Bala Ged Sanctuary', 'Mouth // Feed', 'Divination',
                     'Giant Growth', 'Unsummon', 'Serra Angel'):
            self.add(session.engine, name, zone='graveyard')
        self.assert_closed(session, spell)
        program = next(program for program in session.engine.semantics.programs_for_oracle(spell.oracle_id)
                       if DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY in program.capability_dependencies)
        dependencies = tuple(value for value in program.capability_dependencies
                             if value != DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY)
        altered = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + DISTINCT_PUBLIC_CHARACTERISTIC_COUNT_CAPABILITY,
                      binding['blockers'])

        self.prepare(session, mana={'C': 2, 'U': 1, 'R': 1})
        self.checkpoint(session)
        self.cast(session, 'A', spell)
        self.assertFalse(any(session.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield', session.state.cards[spell.object_id].zone)
        self.replay(session)

    def test_original_selvala_produces_the_chosen_color_for_distinct_power(self):
        session = self.session(608_208_273)
        source = self.add(session.engine, 'Selvala, Eager Trailblazer')
        source.acquired_control_turn_count = -1
        self.add(session.engine, 'Grizzly Bears', ref='selvala-bear-first')
        self.add(session.engine, 'Grizzly Bears', ref='selvala-bear-second')
        self.add(session.engine, 'Ornithopter')
        self.add(session.engine, 'Hill Giant', seat='C')
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offer = next(row for row in actions if row['id'].startswith(f'activate:{source.ref}:'))
        self.assertEqual(3, offer['choice_schema']['mana_output']['total'])
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:A', {'action_id': offer['id'], 'mana_output': {'G': 2, 'U': 1}})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id': offer['id'], 'mana_output': {'G': 3}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, session.state.players['A'].mana_pool['G'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)
