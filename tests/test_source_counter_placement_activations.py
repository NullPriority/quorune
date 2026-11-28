from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import ActivatedAbility
from quorune.activated_ability_descriptor import (
    SOURCE_COUNTER_PLACEMENT_CAPABILITY,
    SOURCE_COUNTER_PLACEMENT_COST,
    SOURCE_COUNTER_PLACEMENT_FIELD,
    SOURCE_COUNTER_PLACEMENT_OFFER_FIELD,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.model import ReplacementEffectError
from quorune.replacement.replay import ReplacementContinuation
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_fixed_counter_additional_costs as counter_witnesses
import test_reflexive_payments as fixture_witnesses


ORIGINALS = ("Devoted Druid", "Barrenton Medic", "Cinderhaze Wretch")
TEXT = "Put a -1/-1 counter on this creature: Untap this creature."
PRICE = SOURCE_COUNTER_PLACEMENT_COST.to_dict()
PROFILE = "commander_review"


class SourceCounterPlacementCompilerTests(unittest.TestCase):
    def test_fixed_price_round_trips_and_the_older_descriptor_shape_is_unchanged(self):
        card = replace(
            record("{T}: Add {G}.\n" + TEXT),
            type_line="Creature - Elf Druid", power="0", toughness="2",
        )
        ordinary, paid = compile_activated_ability_catalog(card)["front"]
        self.assertEqual(SOURCE_COUNTER_PLACEMENT_COST, paid.source_counter_placement_cost)
        self.assertTrue(paid.compiled_cost)
        self.assertIsNone(paid.target_schema)
        saved = paid.to_dict()
        self.assertEqual(PRICE, saved[SOURCE_COUNTER_PLACEMENT_FIELD])
        self.assertEqual(saved, ActivatedAbility.from_dict(saved).to_dict())
        old = ordinary.to_dict()
        self.assertNotIn(SOURCE_COUNTER_PLACEMENT_FIELD, old)
        self.assertEqual(old, ActivatedAbility.from_dict(old).to_dict())
        for changed in (
            {**PRICE, "counter_name": "+1/+1"},
            {**PRICE, "recipient_scope": "opponent_creature"},
            {**PRICE, "schema_version": True},
            {**PRICE, "amount": 2},
            None,
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                ActivatedAbility.from_dict({**saved, SOURCE_COUNTER_PLACEMENT_FIELD: changed})
        with self.assertRaises(ValueError):
            ActivatedAbility.from_dict({**saved, "tap_source": True})

    def test_compiles_only_the_complete_self_price_and_same_source_result(self):
        registry = load_default_capability_registry()
        card = replace(record(TEXT), type_line="Creature - Elf Druid", power="0", toughness="2")
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(SOURCE_COUNTER_PLACEMENT_CAPABILITY, node.capability_dependencies)
        self.assertEqual(PRICE, node.cost[SOURCE_COUNTER_PLACEMENT_FIELD])
        self.assertEqual(({"op":"untap","card":"$source.zone_object"},), node.effects)
        for altered in (
            TEXT.replace("-1/-1", "+1/+1"),
            TEXT.replace("on this creature", "on a creature you control"),
            TEXT.replace(": Untap this creature.", ": Draw a card."),
            "{1}, " + TEXT,
            TEXT + " Activate only as a sorcery.",
        ):
            with self.subTest(text=altered):
                changed = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=registry,
                                              capability_profile=PROFILE)
                self.assertFalse(any(SOURCE_COUNTER_PLACEMENT_CAPABILITY in item.capability_dependencies
                                     for face in changed.faces for item in face.nodes))


class SourceCounterPlacementGameplayTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    witnesses = fixture_witnesses.ReflexivePaymentRuntimeTests
    session = witnesses.session
    add = witnesses.add
    prepare = witnesses.prepare
    choose = witnesses.choose
    checkpoint = witnesses.checkpoint
    resolve = witnesses.resolve
    replay = witnesses.replay
    assert_normal_admission = counter_witnesses.CounterCostReplacementChainTests.assert_normal_admission

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-counter-placement.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "counter-prohibition-cards.json",
            fixtures / "counter-replacement-cards.json",
            fixtures / "source-counter-placement-activation-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source counter payment", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 16),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def offer(self, game, source):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row.get("source") == source.ref
                     and SOURCE_COUNTER_PLACEMENT_OFFER_FIELD in row.get("cost_summary", {})), None)

    def activate(self, game, offer):
        self.assertIsNotNone(offer)
        self.assertEqual(PRICE, offer["cost_summary"][SOURCE_COUNTER_PLACEMENT_OFFER_FIELD])
        self.assertNotIn("cost_cards", offer.get("choice_schema") or {})
        result = game.act("pilot:A", {"action_id": offer["id"], "pay":"auto"})
        self.assertTrue(result.ok, result.summary)

    def scene(self, seed, name="Devoted Druid", replacements=(), initial_counter=0):
        game = self.session(seed)
        source = self.add(game.engine, name)
        source.tapped = True
        if initial_counter:
            source.counters["-1/-1"] = initial_counter
        for permanent, seat in replacements:
            self.add(game.engine, permanent, seat=seat)
        self.prepare(game)
        self.checkpoint(game)
        return game, source

    def test_originals_pay_on_their_own_source_before_untap_and_replay(self):
        for index, name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                game, source = self.scene(618110 + index, name)
                self.assert_normal_admission(game, source)
                before = self.offer(game, source)
                self.activate(game, before)
                item, = game.state.stack
                self.assertEqual((source.logical_object_id, []), (item.context["source_logical_object_id"], item.targets))
                paid = game.state.cards[source.object_id]
                self.assertEqual(1, paid.counters["-1/-1"])
                self.assertTrue(paid.tapped)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.cards[source.object_id].tapped)
                self.replay(game)

    def test_lethal_payment_moves_the_source_before_anyone_can_resolve_untap(self):
        game, source = self.scene(618114, initial_counter=1)
        self.activate(game, self.offer(game, source))
        item, = game.state.stack
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotEqual(game.state.cards[source.object_id].logical_object_id, item.context["source_logical_object_id"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game)

    def test_prohibited_payment_is_unavailable_but_replacement_to_zero_still_pays(self):
        game, source = self.scene(618115, replacements=(("Solemnity", "B"),))
        self.assertIsNone(self.offer(game, source))
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id":f"activate:{source.ref}:ab2", "pay":"auto"})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

        game, source = self.scene(618116, replacements=(("Vorinclex, Monstrous Raider", "B"),))
        self.activate(game, self.offer(game, source))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("-1/-1", 0))
        self.assertEqual(1, len(game.state.stack))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.replay(game)

    def test_wrong_actor_and_payment_object_cannot_change_state_and_failed_commit_rolls_back(self):
        game, source = self.scene(618117)
        offer = self.offer(game, source)
        before = authoritative_state_hash(game.state)
        response = {"action_id":offer["id"],"pay":"auto"}
        for principal, extra in (("pilot:B", {}), ("pilot:A", {"cost_cards":[source.ref]})):
            denied = game.act(principal, {**response, **extra})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))

        from quorune import counter_placement
        original_commit = counter_placement.commit_prepared_counter_placements

        def fail_after_counter(*args, **kwargs):
            result = original_commit(*args, **kwargs)
            self.assertEqual(1, result[0].placed)
            raise GameRuleError("Injected post-payment fault")

        with patch.object(counter_placement, "commit_prepared_counter_placements", side_effect=fail_after_counter):
            denied = game.act("pilot:A", response)
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.stack)
        self.assertEqual({}, game.state.cards[source.object_id].counters)
        self.activate(game, offer)
        self.assertIsNone(self.resolve(game))
        self.replay(game)

    def test_pending_replacement_is_pinned_to_the_same_activation_and_source(self):
        game, source = self.scene(618118, replacements=(
            ("Doc Samson, Super Psychiatrist", "A"), ("Vorinclex, Monstrous Raider", "B"),
        ))
        self.activate(game, self.offer(game, source))
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertFalse(game.state.stack)
        self.assertEqual({}, game.state.cards[source.object_id].counters)
        continuation = game.state.pending_decision.continuation
        restored = ReplacementContinuation.from_dict(continuation)
        self.assertEqual("priority_action_cost", restored.resume_kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        increase, = (card.ref for card in game.state.cards.values()
                     if card.printed_name == "Doc Samson, Super Psychiatrist")
        plus_one, = (row["id"] for row in options if increase in row["id"])
        other_packet = game.packet("pilot:B", full=True)
        self.assertIsNone(other_packet["decision"])
        for field, value in (("requested_amount",2), ("source","wrong-source"), ("target_logical_object_id","wrong@0")):
            forged = deepcopy(continuation)
            forged["replacement_batch"]["events"][0]["payload"][field] = value
            with self.subTest(field=field), self.assertRaises(ReplacementEffectError):
                ReplacementContinuation.from_dict(forged)
        game = self.replay(game, load=True)
        self.choose(game, replacement=plus_one)
        self.assertEqual(1, game.state.cards[source.object_id].counters["-1/-1"])
        self.assertEqual(1, len(game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.replay(game)

    def test_price_identity_is_required_by_the_registered_activation(self):
        game, source = self.scene(618119)
        program, = (value for value in game.engine.semantics.programs_for_oracle(source.oracle_id)
                   if SOURCE_COUNTER_PLACEMENT_CAPABILITY in value.capability_dependencies)
        body = replace(program, effects=({"op":"tap", "card":"$source.zone_object"},))
        binding = bind_semantic_program_runtime(body, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        for changed in ({"mana": []}, {"text": "No price"}, {"mana": {"G": 1}}):
            with self.subTest(changed=changed):
                invalid = replace(program, cost_schema={**program.cost_schema, **changed})
                binding = bind_semantic_program_runtime(invalid, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
        deps = tuple(cap for cap in program.capability_dependencies if cap != SOURCE_COUNTER_PLACEMENT_CAPABILITY)
        undeclared = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(undeclared, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + SOURCE_COUNTER_PLACEMENT_CAPABILITY, binding["blockers"])

    def test_runtime_mutation_that_skips_placement_fails_a_real_payment_assertion(self):
        def witness(seed):
            game, source = self.scene(seed)
            self.activate(game, self.offer(game, source))
            self.assertEqual(1, game.state.cards[source.object_id].counters.get("-1/-1", 0))

        witness(618120)
        with patch("quorune.rules.activation.counter_costs.pay_source_counter_placement", return_value=None):
            with self.assertRaisesRegex(AssertionError, "1 != 0"):
                witness(618121)
