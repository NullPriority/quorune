"""CR 601.2h/608.2h/707.10: results read the permanent actually paid."""

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
from quorune.paid_sacrifice_conditions import (
    PAID_SACRIFICE_CHARACTERISTICS_CONTEXT, PAID_SACRIFICE_CONDITION_CAPABILITY,
    PaidSacrificeCharacteristicCondition, PaidSacrificeCharacteristics,
)
from quorune.query_effect_amount_model import PublicQueryAmountError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.scalar_effect_amount_model import (
    PAID_SACRIFICE_MANA_VALUE_CAPABILITY, PAID_SACRIFICE_MANA_VALUE_CONTEXT,
    PAID_SACRIFICE_MANA_VALUE_MECHANIC, PaidSacrificeManaValueSnapshot,
    PAID_SACRIFICE_STAT_CAPABILITY, PAID_SACRIFICE_STAT_CONTEXT,
    PaidCreatureStatSnapshot, ScalarAmountOrigin, ScalarEffectAmountSpec,
)
from quorune.scalar_effect_amounts import resolve_paid_sacrifice_condition, resolve_scalar_effect_amount
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


class PaidSacrificeCharacteristicTests(unittest.TestCase):
    def test_paid_characteristic_requires_original_fixed_price_and_exact_branches(self):
        from quorune.rules.resolution_condition_shapes import bound_resolution_condition_runtime_dependencies

        price = "As an additional cost to cast this spell, sacrifice a creature.\n"
        body = "Draw two cards. If the sacrificed creature was legendary, draw three cards instead."
        registry = load_default_capability_registry()

        def compile_source(text, capabilities=registry):
            return compile_oracle_card(record(text), capability_registry=capabilities, capability_profile="commander_review")

        ir = compile_source(price + body)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertEqual(price + body, node.text)
        self.assertEqual(PaidSacrificeCharacteristicCondition("creature", "legendary").reference(), effect["cast_fact"])
        self.assertEqual((3, 2), (effect["effects"][0]["count"], effect["otherwise_effects"][0]["count"]))
        self.assertIn(PAID_SACRIFICE_CONDITION_CAPABILITY, node.capability_dependencies)
        valid = SimpleNamespace(effects=node.effects, target_schema=node.target_schema, cost_schema=node.cost, coverage=node.mechanics)
        self.assertIn(PAID_SACRIFICE_CONDITION_CAPABILITY, bound_resolution_condition_runtime_dependencies(valid))
        for cost, effects in ((None, node.effects), (node.cost, ({**effect, "cast_fact": True},))):
            forged = SimpleNamespace(**{**vars(valid), "cost_schema": cost, "effects": effects})
            with self.assertRaises(ValueError):
                bound_resolution_condition_runtime_dependencies(forged)

        for unsupported in (body, price.replace("a creature", "an artifact") + body,
                            price + body.replace("legendary", "mystical"),
                            price + body.replace("draw three cards instead", "exile target artifact instead")):
            with self.subTest(text=unsupported):
                self.assertNotEqual("exact", compile_source(unsupported).status)

        payload = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf8"))
        capability = next(row for row in payload["capabilities"] if row["id"] == PAID_SACRIFICE_CONDITION_CAPABILITY)
        capability.update(status="blocked", blockers=["withdrawn paid characteristic authority"])
        withdrawn = CapabilityRegistry(payload)
        withdrawn.mark_evidence_verified(registry.evidence_fingerprint)
        self.assertNotEqual("exact", compile_source(price + body, withdrawn).status)

    def test_paid_characteristic_receipt_checks_original_incarnation(self):
        context_key = PAID_SACRIFICE_CHARACTERISTICS_CONTEXT
        receipt = PaidSacrificeCharacteristics("paid@1", ("artifact",), ("vehicle",), ("legendary",)).to_dict()
        item = SimpleNamespace(kind="spell", context={
            context_key: receipt, "fixed_cast_lifecycle_cost_objects": [{"logical_object_id": "paid@1"}],
        })
        reference = PaidSacrificeCharacteristicCondition("permanent", "legendary").reference()

        def witness():
            self.assertTrue(resolve_paid_sacrifice_condition(reference, item))

        witness()
        with patch("quorune.paid_sacrifice_conditions.paid_sacrifice_trait_field", return_value="types"):
            with self.assertRaises(AssertionError):
                witness()
        self.assertTrue(resolve_paid_sacrifice_condition(
            PaidSacrificeCharacteristicCondition("permanent", "vehicle").reference(), item))
        self.assertFalse(resolve_paid_sacrifice_condition(
            PaidSacrificeCharacteristicCondition("permanent", "creature").reference(), item))
        copied = SimpleNamespace(kind="spell_copy", context=declared_cost_copy_fields(item.context))
        self.assertTrue(resolve_paid_sacrifice_condition(reference, copied))
        for malformed in (None, {**receipt, "logical_object_id": "paid@2"},
                          {**receipt, "types": ["Artifact"]}, {**receipt, "subtypes": ["vehicle", "vehicle"]}):
            item.context[context_key] = malformed
            with self.assertRaises(ValueError):
                resolve_paid_sacrifice_condition(reference, item)


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


class PaidSacrificeManaValueTests(unittest.TestCase):
    def test_paid_mana_value_requires_the_matching_price_and_trusted_authority(self):
        price = 'As an additional cost to cast this spell, sacrifice an artifact.\n'
        result = "Draw cards equal to the sacrificed artifact's mana value."
        registry = load_default_capability_registry()

        def compile_source(text, capabilities=registry):
            return compile_oracle_card(
                record(text), capability_registry=capabilities, capability_profile='commander_review',
            )

        ir = compile_source(price + result)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(price + result, node.text)
        self.assertIn(PAID_SACRIFICE_MANA_VALUE_CAPABILITY, node.capability_dependencies)
        spec = ScalarEffectAmountSpec.from_dict(node.effects[0]['count'])
        self.assertEqual((ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE, 'mana_value', 12),
                         (spec.origin, spec.characteristic, spec.schema_version))
        for text in (
            result,
            price.replace('an artifact', 'a creature') + result,
            price.replace('an artifact', 'an artifact or creature') + result,
            price + "You gain life equal to the sacrificed artifact's mana value. Draw X cards.",
            price + "Add X mana of any one color, where X is the sacrificed artifact's mana value.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_source(text).status)

        payload = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        cap = next(row for row in payload['capabilities'] if row['id'] == PAID_SACRIFICE_MANA_VALUE_CAPABILITY)
        cap.update(status='blocked', blockers=['withdrawn paid mana value authority'])
        withdrawn = CapabilityRegistry(payload)
        withdrawn.mark_evidence_verified(registry.evidence_fingerprint)
        self.assertNotEqual('exact', compile_source(price + result, withdrawn).status)

    def test_paid_mana_value_receipt_rejects_wrong_incarnation_and_kills_zero_mutant(self):
        snapshot = PaidSacrificeManaValueSnapshot('object@1', 4).to_dict()
        item = SimpleNamespace(kind='spell', controller='A', context={
            PAID_SACRIFICE_MANA_VALUE_CONTEXT: snapshot,
            'fixed_cast_lifecycle_cost_objects': [{'logical_object_id': 'object@1'}],
        })
        value = ScalarEffectAmountSpec(
            ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE, characteristic='mana_value', schema_version=12,
        ).to_dict()

        def witness():
            self.assertEqual(4, resolve_scalar_effect_amount(None, value, item))

        witness()
        with patch('quorune.scalar_effect_amounts._paid_sacrifice_mana_value', return_value=0):
            with self.assertRaises(AssertionError):
                witness()
        for malformed in (None, {**snapshot, 'mana_value': True}, {**snapshot, 'mana_value': -1},
                          {**snapshot, 'logical_object_id': 'object@2'}):
            item.context[PAID_SACRIFICE_MANA_VALUE_CONTEXT] = malformed
            with self.assertRaises(PublicQueryAmountError):
                resolve_scalar_effect_amount(None, value, item)


class PaidSacrificeCreatureNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'paid-creature-stats.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/paid-sacrifice-creature-stat-cards.json',
            ROOT / 'tests/fixtures/paid-sacrifice-mana-value-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Paid creature stats', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 25),
        ], ['Generic Bound Commander'])

    def cast_with_sacrifice(self, session, spell, permanent, targets=(), invalid_price=None):
        self.priority(session, 'A')
        action = self.action(session, 'A', spell)
        command = {'action_id': action['id'], 'sacrifice_cards': [permanent.ref], 'pay': 'auto'}
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

    def test_original_nasty_end_and_copy_read_the_same_legendary_price(self):
        for legendary in (False, True):
            with self.subTest(legendary=legendary):
                session = self.session(704_601_240 + legendary)
                source = self.add(session.engine, "Nasty End", zone="hand")
                paid = self.add(session.engine, "Isamaru, Hound of Konda" if legendary else "Grizzly Bears",
                                ref="nasty-end-price")
                foreign = self.add(session.engine, "Grizzly Bears", seat="C", ref="nasty-end-foreign-price")
                if legendary:
                    copier = self.add(session.engine, "Twincast", seat="B", zone="hand")
                    for index in range(2):
                        self.add(session.engine, "Island", seat="B", ref=f"nasty-end-copy-island-{index}")
                self.assert_closed(session, source)
                self.prepare(session, mana={"B": 1, "C": 1})
                self.checkpoint(session)
                hands = {seat: set(session.state.players[seat].zones["hand"]) for seat in "AB"}
                identity = paid.logical_object_id
                original = self.cast_with_sacrifice(session, source, paid, invalid_price=foreign)
                receipt = original.context[PAID_SACRIFICE_CHARACTERISTICS_CONTEXT]
                self.assertEqual(identity, receipt["logical_object_id"])
                self.assertEqual(legendary, "legendary" in receipt["supertypes"])
                self.assertEqual("graveyard", session.state.cards[paid.object_id].zone)
                if legendary:
                    self.cast_for(session, "B", copier, [original.ref])
                    self.resolve_top(session)
                    copied = session.state.stack[-1]
                    self.assertEqual(("spell_copy", "B"), (copied.kind, copied.controller))
                    self.assertEqual(receipt, copied.context[PAID_SACRIFICE_CHARACTERISTICS_CONTEXT])
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                for seat, count in (("A", 3 if legendary else 2), ("B", 3 if legendary else 0)):
                    drawn = set(session.state.players[seat].zones["hand"]) - hands[seat]
                    self.assertEqual(count, len(drawn))
                    for viewer in set("ABCD") - {seat}:
                        packet = json.dumps(session.packet("pilot:" + viewer, full=True))
                        for object_id in drawn:
                            self.assertNotIn(session.state.cards[object_id].ref, packet)
                self.replay(session)

    def test_original_foundry_helix_uses_the_effective_paid_artifact_type(self):
        from quorune.continuous_effect_model import ContinuousOperation, Layer
        from quorune.continuous_effect_state import (
            ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
        )

        for granted in (False, True):
            with self.subTest(granted=granted):
                session = self.session(704_601_242 + granted)
                source = self.add(session.engine, "Foundry Helix", zone="hand")
                paid = self.add(session.engine, "Grizzly Bears")
                self.add(session.engine, "Sol Ring")
                self.assert_closed(session, source)
                if granted:
                    create_resolution_continuous_effect_components(
                        session.engine, source=ResolutionEffectSource(stack_ref="paid-artifact-type-witness"),
                        targets=(paid,), components=(ResolutionContinuousComponent(Layer.TYPE, "4", (
                            ContinuousOperation("add_types", ("artifact",), field="card_types"),
                        )),),
                    )
                self.prepare(session, mana={"W": 1, "R": 1, "C": 1})
                self.checkpoint(session)
                original = self.cast_with_sacrifice(session, source, paid, ["C"])
                receipt = original.context[PAID_SACRIFICE_CHARACTERISTICS_CONTEXT]
                self.assertEqual(granted, "artifact" in receipt["types"])
                self.assertNotIn("Artifact", session.engine.card_record(paid).type_line)
                self.assertIsNone(self.resolve(session))
                self.assertEqual([44 if granted else 40, 40, 36, 40],
                                 [session.state.players[seat].life for seat in "ABCD"])
                self.replay(session)

    def test_original_paid_vehicle_and_legendary_artifact_keep_their_fixed_results(self):
        cases = (
            ("Hellish Sideswipe", "Sky Skiff", {"B": 1}, "subtypes", "vehicle"),
            ("Splitting the Powerstone", "Mox Opal", {"U": 1, "C": 2}, "supertypes", "legendary"),
        )
        for index, (name, paid_name, mana, field, trait) in enumerate(cases):
            with self.subTest(card=name):
                session = self.session(704_601_244 + index)
                source = self.add(session.engine, name, zone="hand")
                paid = self.add(session.engine, paid_name)
                target = self.add(session.engine, "Darksteel Myr", seat="C") if index == 0 else None
                self.assert_closed(session, source)
                self.prepare(session, mana=mana)
                self.checkpoint(session)
                before = set(session.state.cards)
                hand = set(session.state.players["A"].zones["hand"])
                original = self.cast_with_sacrifice(session, source, paid, [target.ref] if target else ())
                self.assertIn(trait, original.context[PAID_SACRIFICE_CHARACTERISTICS_CONTEXT][field])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(1, len(set(session.state.players["A"].zones["hand"]) - hand))
                if target:
                    self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
                else:
                    created = [session.state.cards[object_id] for object_id in session.state.cards.keys() - before]
                    self.assertEqual(2, len(created))
                    self.assertTrue(all(card.tapped and card.zone == "battlefield" and card.controller == "A"
                                        and "Powerstone" in card.printed_name for card in created))
                self.replay(session, load=True)

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
        original = self.cast_with_sacrifice(session, spell, creature)
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
                item = self.cast_with_sacrifice(session, spell, creature, [target], invalid_price=foreign)
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

        first = self.cast_with_sacrifice(session, carrion, giant)
        self.assertEqual(3, first.context[PAID_SACRIFICE_STAT_CONTEXT]['power'])
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield']
        self.assertEqual(['A'] * 3, [card.controller for card in tokens])

        second = self.cast_with_sacrifice(session, explosion, first_bear)
        self.assertEqual(2, second.context[PAID_SACRIFICE_STAT_CONTEXT]['power'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[second_bear.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[wall.object_id].zone)
        self.assertEqual(2, session.engine._numeric_stat(wall.object_id, 'toughness'))
        self.assertFalse(any(card.is_token and card.zone == 'battlefield' for card in session.state.cards.values()))
        self.replay(session)

    def test_original_morbid_curiosity_and_copy_draw_from_the_paid_artifact(self):
        session = self.session(704_601_211)
        spell = self.add(session.engine, 'Morbid Curiosity', zone='hand')
        artifact = self.add(session.engine, 'Thran Dynamo')
        foreign = self.add(session.engine, 'Sol Ring', seat='D')
        copier = self.add(session.engine, 'Twincast', seat='B', zone='hand')
        self.add(session.engine, 'Island', seat='B', ref='mana-value-copy-island-1')
        self.add(session.engine, 'Island', seat='B', ref='mana-value-copy-island-2')
        for source in (spell, artifact, copier):
            self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'B': 2})
        self.checkpoint(session)
        hands = {seat: set(session.state.players[seat].zones['hand']) for seat in 'AB'}
        snapshot = PaidSacrificeManaValueSnapshot(artifact.logical_object_id, 4).to_dict()
        original = self.cast_with_sacrifice(session, spell, artifact, invalid_price=foreign)
        self.assertEqual(snapshot, original.context[PAID_SACRIFICE_MANA_VALUE_CONTEXT])
        self.assertEqual('graveyard', session.state.cards[artifact.object_id].zone)
        self.assertEqual('battlefield', session.state.cards[foreign.object_id].zone)

        self.cast_for(session, 'B', copier, [original.ref])
        self.resolve_top(session)
        copied = session.state.stack[-1]
        self.assertEqual(('spell_copy', 'B'), (copied.kind, copied.controller))
        self.assertEqual(snapshot, copied.context[PAID_SACRIFICE_MANA_VALUE_CONTEXT])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        for seat in 'AB':
            drawn = set(session.state.players[seat].zones['hand']) - hands[seat]
            self.assertEqual(4, len(drawn))
            for viewer in set('ABCD') - {seat}:
                packet = json.dumps(session.packet('pilot:' + viewer, full=True))
                for oid in drawn:
                    self.assertNotIn(session.state.cards[oid].ref, packet)
        self.replay(session)

    def test_original_reckoners_bargain_reads_the_paid_value_after_destination_replacement(self):
        session = self.session(704_601_212)
        spell = self.add(session.engine, "Reckoner's Bargain", zone='hand')
        artifact = self.add(session.engine, 'Thran Dynamo')
        self.add(session.engine, 'Dauthi Voidwalker', seat='B')
        self.assert_closed(session, spell)
        self.prepare(session, mana={'C': 1, 'B': 1})
        self.checkpoint(session)
        size = len(session.state.players['A'].zones['hand'])
        original = self.cast_with_sacrifice(session, spell, artifact)
        self.assertEqual(4, original.context[PAID_SACRIFICE_MANA_VALUE_CONTEXT]['mana_value'])
        self.assertEqual('exile', session.state.cards[artifact.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(44, session.state.players['A'].life)
        self.assertEqual(size + 1, len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_original_forge_armor_keeps_payment_when_its_target_leaves(self):
        for target_leaves in (False, True):
            with self.subTest(target_leaves=target_leaves):
                session = self.session(704_601_213 + target_leaves)
                spell = self.add(session.engine, 'Forge Armor', zone='hand')
                artifact = self.add(session.engine, 'Thran Dynamo')
                body = self.add(session.engine, 'Grizzly Bears')
                bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand')
                self.add(session.engine, 'Island', seat='B')
                self.assert_closed(session, spell)
                self.prepare(session, mana={'C': 4, 'R': 1})
                self.checkpoint(session)
                self.cast_with_sacrifice(session, spell, artifact, [body.ref], invalid_price=body)
                if target_leaves:
                    self.cast_for(session, 'B', bounce, [body.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard', session.state.cards[artifact.object_id].zone)
                card = session.state.cards[body.object_id]
                self.assertEqual(('hand', 0) if target_leaves else ('battlefield', 4),
                                 (card.zone, card.counters.get('+1/+1', 0)))
                self.replay(session, load=True)

    def test_original_sacrifice_adds_black_mana_only_for_the_paid_creature_value(self):
        session = self.session(704_601_215)
        zero_spell = self.add(session.engine, 'Sacrifice', zone='hand', ref='zero-mana-value-sacrifice')
        positive_spell = self.add(session.engine, 'Sacrifice', zone='hand', ref='positive-mana-value-sacrifice')
        producer = self.add(session.engine, 'Raise the Alarm', zone='hand')
        body = self.add(session.engine, 'Grizzly Bears')
        for source in (zero_spell, producer, body):
            self.assert_closed(session, source)
        copy_ref, = session.engine.create_token('A', name='', copy_of=body.ref, reason='paid-value fixture')
        copied_creature = next(card for card in session.state.cards.values() if card.ref == copy_ref)
        self.prepare(session, mana={'C': 1, 'W': 1, 'B': 2})
        self.checkpoint(session)
        self.cast_for(session, 'A', producer, reserve={'B': 2})
        self.assertIsNone(self.resolve(session))
        token = next(card for card in session.state.cards.values()
                     if card.is_token and card.zone == 'battlefield' and card.ref != copy_ref)

        first = self.cast_with_sacrifice(session, zero_spell, token)
        self.assertEqual(0, first.context[PAID_SACRIFICE_MANA_VALUE_CONTEXT]['mana_value'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.state.players['A'].mana_pool['B'])

        second = self.cast_with_sacrifice(session, positive_spell, copied_creature)
        self.assertEqual(2, second.context[PAID_SACRIFICE_MANA_VALUE_CONTEXT]['mana_value'])
        self.assertEqual(0, session.state.players['A'].mana_pool['B'])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.players['A'].mana_pool['B'])
        self.assertEqual('battlefield', session.state.cards[body.object_id].zone)
        self.replay(session)

    def test_paid_mana_value_runtime_binding_checks_the_cost_and_result_owner(self):
        session = self.session(704_601_216)
        spell = self.add(session.engine, 'Sacrifice', zone='hand')
        self.assert_closed(session, spell)
        program, = session.engine.semantics.programs_for_oracle(spell.oracle_id)
        for changed in (
            replace(program, cost_schema=None),
            replace(program, coverage=tuple(x for x in program.coverage if x != PAID_SACRIFICE_MANA_VALUE_MECHANIC)),
            replace(program, effects=({**program.effects[0], 'player': 'B'},)),
            replace(program, effects=({**program.effects[0], 'spend_restriction': 'spell.types_any=creature'},)),
        ):
            binding = bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile='commander_review',
            )
            self.assertFalse(binding['strict'])
            self.assertTrue(any(blocker.startswith('runtime_typed_result_amount:invalid:')
                                for blocker in binding['blockers']), binding['blockers'])
