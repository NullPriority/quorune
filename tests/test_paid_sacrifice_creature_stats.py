"""CR 601.2h/608.2h/707.10: results read the creature actually paid."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.declared_cast_cost_model import declared_cost_copy_fields
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import PublicQueryAmountError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.scalar_effect_amount_model import (
    PAID_SACRIFICE_STAT_CAPABILITY, PAID_SACRIFICE_STAT_CONTEXT,
    PaidCreatureStatSnapshot, ScalarAmountOrigin, ScalarEffectAmountSpec,
)
from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


class PaidSacrificeCreatureStatTests(unittest.TestCase):
    def test_compiler_requires_the_closed_price_and_paid_stat_capability(self):
        price = 'As an additional cost to cast this spell, sacrifice a creature.\n'
        result = "Generic Bound Compiler Fixture deals damage equal to the sacrificed creature's power to any target."
        registry = load_default_capability_registry()
        def compile_source(text, caps=registry):
            return compile_oracle_card(record(text), capability_registry=caps, capability_profile='commander_review')

        ir = compile_source(price + result)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        spec = ScalarEffectAmountSpec.from_dict(node.effects[0]['amount'])
        self.assertEqual((ScalarAmountOrigin.PAID_SACRIFICE_CREATURE, 'power', 9),
                         (spec.origin, spec.characteristic, spec.schema_version))
        self.assertIn(PAID_SACRIFICE_STAT_CAPABILITY, node.capability_dependencies)
        self.assertEqual(price + result, node.text)
        for text in (result, price.replace('a creature', 'an artifact') + result,
                     price + "Return X target creatures to their owners' hands, where X is the sacrificed creature's power."):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_source(text).status)

        payload = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        cap = next(row for row in payload['capabilities'] if row['id'] == PAID_SACRIFICE_STAT_CAPABILITY)
        cap.update(status='blocked', blockers=['withdrawn paid snapshot authority'])
        withdrawn = CapabilityRegistry(payload)
        withdrawn.mark_evidence_verified(registry.evidence_fingerprint)
        self.assertNotEqual('exact', compile_source(price + result, withdrawn).status)

    def test_snapshot_incarnation_and_stat_selection_fail_closed(self):
        snapshot = PaidCreatureStatSnapshot('object@1', -3, 4).to_dict()
        item = SimpleNamespace(kind='spell', controller='A', context={
            PAID_SACRIFICE_STAT_CONTEXT: snapshot,
            'fixed_cast_lifecycle_cost_objects': [{'logical_object_id': 'object@1'}],
        })
        self.assertEqual({PAID_SACRIFICE_STAT_CONTEXT: snapshot}, declared_cost_copy_fields(item.context))
        power = ScalarEffectAmountSpec(ScalarAmountOrigin.PAID_SACRIFICE_CREATURE,
                                       characteristic='power', schema_version=9).to_dict()
        toughness = {**power, 'characteristic': 'toughness'}

        def witness():
            self.assertEqual(0, resolve_scalar_effect_amount(None, power, item))
            self.assertEqual(4, resolve_scalar_effect_amount(None, toughness, item))

        witness()
        with patch('quorune.scalar_effect_amounts._paid_creature_stat_amount', return_value=4):
            with self.assertRaises(AssertionError):
                witness()
        for malformed in (None, {**snapshot, 'power': True}, {**snapshot, 'logical_object_id': 'object@2'}):
            item.context[PAID_SACRIFICE_STAT_CONTEXT] = malformed
            with self.assertRaises(PublicQueryAmountError):
                resolve_scalar_effect_amount(None, power, item)


class PaidSacrificeCreatureNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'paid-creature-stats.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/paid-sacrifice-creature-stat-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Paid creature stats', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 25),
        ], ['Generic Bound Commander'])

    def cast_with_creature_price(self, session, spell, creature, targets=(), invalid_price=None):
        self.priority(session, 'A')
        action = self.action(session, 'A', spell)
        command = {'action_id': action['id'], 'sacrifice_cards': [creature.ref], 'pay': 'auto'}
        if targets:
            command['targets'] = list(targets)
        if invalid_price is not None:
            before = authoritative_state_hash(session.state)
            denied = session.act('pilot:A', {**command, 'sacrifice_cards': [invalid_price.ref]})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        return next(item for item in session.state.stack if item.card_object_id == spell.object_id)

    def test_original_momentous_fall_and_its_copy_keep_the_two_paid_stats(self):
        session = self.session(704_601_201)
        spell = self.add(session.engine, 'Momentous Fall', zone='hand')
        creature = self.add(session.engine, 'Wall of Omens')
        growth = self.add(session.engine, 'Giant Growth', zone='hand')
        copier = self.add(session.engine, 'Twincast', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B', ref='twincast-island-one')
        self.add(session.engine, 'Island', seat='B', ref='twincast-island-two')
        for source in (spell, creature, growth, copier):
            self.assert_closed(session, source)
        self.prepare(session, mana={'C': 2, 'G': 3})
        self.checkpoint(session)
        self.cast_for(session, 'A', growth, [creature.ref])
        self.assertIsNone(self.resolve(session))
        hands = {seat: set(session.state.players[seat].zones['hand']) for seat in 'AB'}
        incarnation = session.state.cards[creature.object_id].logical_object_id
        original = self.cast_with_creature_price(session, spell, creature)
        snapshot = PaidCreatureStatSnapshot(incarnation, 3, 7).to_dict()
        self.assertEqual(snapshot, original.context[PAID_SACRIFICE_STAT_CONTEXT])
        self.assertEqual('graveyard', session.state.cards[creature.object_id].zone)

        self.cast_for(session, 'B', copier, [original.ref])
        self.resolve_top(session)
        copied = session.state.stack[-1]
        self.assertEqual(('spell_copy', 'B'), (copied.kind, copied.controller))
        self.assertEqual(snapshot, copied.context[PAID_SACRIFICE_STAT_CONTEXT])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([47, 47, 40, 40], [session.state.players[seat].life for seat in 'ABCD'])
        for seat in 'AB':
            drawn = set(session.state.players[seat].zones['hand']) - hands[seat]
            self.assertEqual(3, len(drawn))
            for viewer in set('ABCD') - {seat}:
                packet = json.dumps(session.packet('pilot:' + viewer, full=True))
                for oid in drawn:
                    self.assertNotIn(session.state.cards[oid].ref, packet)
        self.replay(session)

    def test_original_fling_handles_negative_power_and_a_sacrificed_target(self):
        for negative in (False, True):
            with self.subTest(negative=negative):
                session = self.session(704_601_202 + negative)
                spell = self.add(session.engine, 'Fling', zone='hand')
                creature = self.add(session.engine, 'Grizzly Bears')
                foreign = self.add(session.engine, 'Grizzly Bears', seat='B', ref='foreign-price')
                shrink = self.add(session.engine, 'Hydrosurge', seat='B', zone='hand')
                self.add(session.engine, 'Island', seat='B')
                self.assert_closed(session, spell)
                self.prepare(session, mana={'C': 1, 'R': 1})
                self.checkpoint(session)
                if negative:
                    self.cast_for(session, 'B', shrink, [creature.ref])
                    self.assertIsNone(self.resolve(session))
                target = 'C' if negative else creature.ref
                item = self.cast_with_creature_price(session, spell, creature, [target], invalid_price=foreign)
                self.assertEqual(-3 if negative else 2, item.context[PAID_SACRIFICE_STAT_CONTEXT]['power'])
                self.assertEqual('graveyard', session.state.cards[creature.object_id].zone)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual([40] * 4, [session.state.players[seat].life for seat in 'ABCD'])
                self.assertFalse(any(event.code == 'effect.damage' for event in session.state.events))
                self.replay(session)

    def test_bound_spell_requires_the_original_price_at_admission(self):
        session = self.session(704_601_205)
        source = self.add(session.engine, 'Fling', zone='hand')
        self.assert_closed(session, source)
        program, = session.engine.semantics.programs_for_oracle(source.oracle_id)
        binding = bind_semantic_program_runtime(replace(program, cost_schema=None),
            capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertTrue(any(blocker.startswith('runtime_typed_result_amount:invalid:') for blocker in binding['blockers']))

    def test_original_carrion_and_ichor_explosion_use_their_own_paid_creatures(self):
        session = self.session(704_601_206)
        carrion = self.add(session.engine, 'Carrion', zone='hand')
        explosion = self.add(session.engine, 'Ichor Explosion', zone='hand')
        giant = self.add(session.engine, 'Hill Giant')
        first_bear = self.add(session.engine, 'Grizzly Bears')
        second_bear = self.add(session.engine, 'Grizzly Bears', seat='B', ref='explosion-recipient')
        wall = self.add(session.engine, 'Wall of Omens', seat='C')
        self.assert_closed(session, carrion)
        self.assert_closed(session, explosion)
        self.prepare(session, mana={'C': 6, 'B': 4})
        self.checkpoint(session)

        first = self.cast_with_creature_price(session, carrion, giant)
        self.assertEqual(3, first.context[PAID_SACRIFICE_STAT_CONTEXT]['power'])
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(['A'] * 3, [card.controller for card in tokens])

        second = self.cast_with_creature_price(session, explosion, first_bear)
        self.assertEqual(2, second.context[PAID_SACRIFICE_STAT_CONTEXT]['power'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[second_bear.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[wall.object_id].zone)
        self.assertEqual(2, session.engine._numeric_stat(wall.object_id, 'toughness'))
        self.assertFalse(any(card.is_token and card.zone == 'battlefield' for card in session.state.cards.values()))
        self.replay(session)
