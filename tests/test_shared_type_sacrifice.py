"""Independent native witnesses for the shared-type sacrifice instruction."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.shared_type_sacrifice import (
    SHARED_TYPE_SACRIFICE_CAPABILITY, SacrificedCardTypes, shared_type_sacrifice_effect,
    validate_shared_type_sacrifice,
)
from scripts.build_test_database import build_fixture_database


CARD = "Braids, Arisen Nightmare"
PROFILE = "commander_review"


class SharedTypeSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "shared-type-sacrifice.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "committed-apnap-sacrifice-token-count-cards.json",
            fixtures / "shared-type-sacrifice-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Shared-type sacrifice", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def begin(self, game, source):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda g: g.state.step == "end_step" and self.trigger_count(g, source) == 1)
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (decision.kind, decision.actors))
        self.assertEqual("A", game.state.active_player)

    def options(self, game, seat):
        decision = game.state.pending_decision
        self.assertEqual(("choice.apnap", [seat]), (decision.kind, decision.actors))
        self.assertIsNone(game.packet("spectator", full=True)["decision"])
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(0, context["minimum"])
        return context

    def choose(self, game, seat, *cards):
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, *cards):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, {
            "action_id": "choose", "cards": [card.ref for card in cards],
        }).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_multitype_replacement_public_apnap_and_private_draw_replay(self):
        game = self.session(20502101)
        source = self.add(game.engine, CARD, seat="C")
        paid = self.add(game.engine, "Darksteel Myr", seat="D")
        game.engine.change_control(source.object_id, "A", reason="Borrowed trigger source fixture")
        game.engine.change_control(paid.object_id, "A", reason="Borrowed artifact creature fixture")
        artifact = self.add(game.engine, "Kefnet's Monument", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        declined = self.add(game.engine, "Grizzly Bears", seat="D", ref="declining-bear")
        land = self.add(game.engine, "Forest", seat="D")
        self.add(game.engine, "Rest in Peace", seat="D")
        self.begin(game, source)
        life_before = {seat: game.state.players[seat].life for seat in "ABCD"}
        hands_before = {seat: tuple(game.state.players[seat].zones["hand"]) for seat in "ABCD"}
        draw_id = game.state.players["A"].zones["library"][-1]

        self.assertEqual({source.ref, paid.ref}, set(self.options(game, "A")["options"]))
        self.reject(game, "B", paid)
        self.reject(game, "A", artifact)
        self.reject(game, "A", source, paid)
        self.choose(game, "A", paid)
        self.assertEqual("exile", game.state.cards[paid.object_id].zone)

        self.assertEqual([artifact.ref], self.options(game, "B")["options"])
        self.choose(game, "B", artifact)
        game = self.replay(game, load=True)
        self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
        c_options = self.options(game, "C")
        self.assertEqual([creature.ref], c_options["options"])
        self.assertEqual({"B": [artifact.ref]}, c_options["prior_public_choices"])
        self.choose(game, "C", creature)
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)

        self.assertEqual([declined.ref], self.options(game, "D")["options"])
        self.reject(game, "D", land)
        offset = len(game.state.events)
        self.choose(game, "D")
        self.assertEqual(["A"], [event.actor for event in game.state.events[offset:] if event.code == "card.draw"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("exile", "exile", "battlefield"), tuple(
            game.state.cards[card.object_id].zone for card in (artifact, creature, declined)
        ))
        self.assertEqual({**life_before, "D": life_before["D"] - 2},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        self.assertEqual(hands_before["A"] + (draw_id,), tuple(game.state.players["A"].zones["hand"]))
        for seat in "BCD":
            self.assertEqual(hands_before[seat], tuple(game.state.players[seat].zones["hand"]))
        drawn_ref = game.state.cards[draw_id].ref
        self.assertIn(drawn_ref, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(drawn_ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_first_decline_stops_and_no_match_counts_each_opponent(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                game = self.session(20502110 + pay)
                source = self.add(game.engine, CARD)
                life_before = {seat: game.state.players[seat].life for seat in "ABCD"}
                self.begin(game, source)
                before = tuple(game.state.players["A"].zones["hand"])
                expected_draw = tuple(reversed(game.state.players["A"].zones["library"][-3:]))
                offset = len(game.state.events)
                self.choose(game, "A", *((source,) if pay else ()))
                self.assertEqual(["A"] * (3 if pay else 0),
                                 [event.actor for event in game.state.events[offset:] if event.code == "card.draw"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard" if pay else "battlefield", game.state.cards[source.object_id].zone)
                for seat in "BCD":
                    self.assertEqual(life_before[seat] - (2 if pay else 0), game.state.players[seat].life)
                self.assertEqual(before + (expected_draw if pay else ()), tuple(game.state.players["A"].zones["hand"]))
                self.replay(game, load=True)

    def test_canonical_copy_uses_new_controller_and_fresh_payment_on_replay(self):
        game = self.session(20502125)
        source = self.add(game.engine, CARD)
        copy_payment = self.add(game.engine, "Forest", seat="B", ref="copy-controller-land")
        original_payment = self.add(game.engine, "Forest", ref="original-controller-land")
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda g: g.state.step == "end_step" and self.trigger_count(g, source) == 1)
        original = game.state.stack[-1]
        # Seed the canonical copy boundary without admitting a utility card.
        copy = game.engine._copy_stack_item(
            controller="B", target=original, targets=(), target_groups={},
            reason="Shared sacrifice copied-controller interaction",
        )
        self.assertEqual(("B", "A", original.ref),
                         (copy.controller, copy.context["player"], copy.context["copied_from_stack"]))
        self.checkpoint(game)
        self.resolve(game)
        self.assertEqual([copy_payment.ref], self.options(game, "B")["options"])
        self.choose(game, "B", copy_payment)
        game = self.replay(game, load=True)
        self.assertEqual([original_payment.ref], self.options(game, "A")["options"])
        offset = len(game.state.events)
        self.choose(game, "A", original_payment)
        self.assertEqual(["B", "B"], [event.actor for event in game.state.events[offset:]
                                     if event.code == "card.draw"])
        self.assertEqual([40, 40, 38, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(("graveyard", "graveyard"), tuple(
            game.state.cards[card.object_id].zone for card in (copy_payment, original_payment)
        ))
        self.resolve(game)
        self.assertEqual([source.ref], self.options(game, "A")["options"])
        self.choose(game, "A")
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_exact_whole_card_binding_fail_closed_and_mutation(self):
        card = self.db.lookup(CARD, fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                    if SHARED_TYPE_SACRIFICE_CAPABILITY in p.capability_dependencies)
        self.assertIsNone(program.target_schema)
        self.assertIsNone(validate_shared_type_sacrifice(program.effects[0], initial_only=True))
        deps = tuple(cap for cap in program.capability_dependencies if cap != SHARED_TYPE_SACRIFICE_CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + SHARED_TYPE_SACRIFICE_CAPABILITY,
                      binding["blockers"])
        for prior, changed in (
            ("your end step", "each end step"), ("2 life", "3 life"),
            ("a card type", "a creature type"), ("permanent of their choice", "target permanent"),
            ("you draw a card.", "you draw a card and add {B}."),
        ):
            self.assertNotEqual("exact", compile_card(replace(card, oracle_text=card.oracle_text.replace(prior, changed))).status)
        with patch("quorune.compiler.shared_type_sacrifice.shared_type_sacrifice_binding_result", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)

    def test_snapshot_retains_extra_card_types_and_rejects_bad_payloads(self):
        paid = SacrificedCardTypes(("artifact", "battle", "kindred"))
        effect = shared_type_sacrifice_effect(actor="A", paid=paid)
        self.assertEqual(paid, validate_shared_type_sacrifice(effect, actor="A"))
        self.assertEqual(["artifact", "battle", "kindred"], effect["predicate"]["types_any"])
        for types in ((), ("battle",), ("artifact", "artifact"), ("legendary",), ("artifact", "equipment")):
            with self.subTest(types=types), self.assertRaises(ValueError):
                SacrificedCardTypes(types)
        with self.assertRaises(ValueError):
            validate_shared_type_sacrifice({**effect, "players": "all"}, actor="A")

    def test_shared_owner_orders_both_arrivals_before_loss_and_card_draw(self):
        game = self.session(404_302_01)
        source = self.add(game.engine, CARD)
        payment = self.add(game.engine, "Darksteel Myr")
        own = self.add(game.engine, "Grizzly Bears", seat="B", ref="STAYS-WITH-OWNER")
        borrowed = self.add(game.engine, "Grizzly Bears", seat="B", ref="SAME-OWNER-DIFFERENT-CONTROLLER")
        game.engine.change_control(borrowed.object_id, "C", reason="Fixture assigns the same owner's second creature")
        self.add(game.engine, "Grizzly Bears", seat="D")
        self.begin(game, source)
        life = game.state.players["D"].life
        hand = tuple(game.state.players["A"].zones["hand"])
        self.choose(game, "A", payment)
        self.choose(game, "B", own)
        self.choose(game, "C", borrowed)
        self.choose(game, "D")
        pending = game.state.pending_decision
        self.assertEqual(("permanent.composition_order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[borrowed.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.assertEqual(life, game.state.players["D"].life)
        self.assertEqual(hand, tuple(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        response = {"action_id": "order", "cards": [borrowed.ref, own.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", response).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", response)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([borrowed.object_id, own.object_id], game.state.players["B"].zones["graveyard"][-2:])
        self.assertEqual(life - 2, game.state.players["D"].life)
        self.assertEqual(len(hand) + 1, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)
