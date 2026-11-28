"""Proof of source-controller draw and life-gain replacement conditions."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.draw_replacements import (
    EMPTY_HAND_DRAW_MULTIPLIER_CAPABILITY,
    EMPTY_HAND_DRAW_MULTIPLIER_HANDLER_ID,
    collect_draw_replacement_effects,
    default_draw_replacement_registry,
)
from quorune.semantic_runtime.life_replacements import (
    LOW_LIFE_GAIN_MULTIPLIER_CAPABILITY,
    LOW_LIFE_GAIN_MULTIPLIER_HANDLER_ID,
    collect_life_change_replacement_effects,
    default_life_replacement_registry,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class ConditionalReplacementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "conditional-replacements.sqlite3"
        root = ROOT / "tests/fixtures"
        build_fixture_database([
            root / "conditional-replacement-cards.json",
            root / "opponent-life-gain-to-loss-cards.json",
            root / "opponent-draw-cards.json",
            root / "redirected-draw-cards.json",
            root / "life-total-setting-cards.json",
            root / "legend-rule-exemption-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Conditional replacements", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_printed_descriptor_and_conditions_are_closed(self):
        card = self.db.lookup("Phial of Galadriel", fuzzy=False)
        result = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        nodes = [node for face in result.faces for node in face.nodes]
        self.assertEqual(3, len(nodes))
        families = (
            ("draw", EMPTY_HAND_DRAW_MULTIPLIER_HANDLER_ID, EMPTY_HAND_DRAW_MULTIPLIER_CAPABILITY,
             default_draw_replacement_registry()),
            ("life.change", LOW_LIFE_GAIN_MULTIPLIER_HANDLER_ID, LOW_LIFE_GAIN_MULTIPLIER_CAPABILITY,
             default_life_replacement_registry()),
        )
        for event, handler, capability, registry in families:
            with self.subTest(event=event):
                node, = (node for node in nodes if node.event == event)
                descriptor, = node.handlers
                self.assertEqual(handler, descriptor["handler_id"])
                self.assertIn(capability, node.capability_dependencies)
                self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, node.runtime_coverage)
                self.assertEqual({"affected_player_relation": "source_controller"}, descriptor["condition"])
                registry.validate(descriptor)
                for invalid in (
                    {**descriptor, "schema_version": True},
                    {**descriptor, "condition": {"affected_player_relation": "any"}},
                    {**descriptor, "modification": {key: 3 for key in descriptor["modification"]}},
                ):
                    with self.assertRaises(SemanticNodeError):
                        registry.validate(invalid)

        binding, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        for event, _, capability, _ in families:
            program, = (program for program in programs if program.event == event)
            dependencies = tuple(dep for dep in program.capability_dependencies if dep != capability)
            dropped = replace(
                program, capability_dependencies=dependencies,
                capability_closure=(self.registry.closure(dependencies, profile="commander_review").to_dict()
                                    if dependencies else None),
            )
            rebound = bind_semantic_program_runtime(dropped, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(rebound["strict"], rebound)
            self.assertIn(f"capability:undeclared_runtime_dependency:{capability}", rebound["blockers"])

        for text in (
            card.oracle_text.replace("no cards in hand", "one card in hand"),
            card.oracle_text.replace("5 or less life", "5 or more life"),
            card.oracle_text.replace("5 or less life", "6 or less life"),
        ):
            with self.subTest(text=text):
                broken = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
                self.assertNotEqual("exact", broken.status)

    def test_missing_threshold_guards_are_detected(self):
        game = self.session(121614500)
        phial = self.add(game.engine, "Phial of Galadriel", ref="threshold-mutation-source")
        self.assert_closed(game, phial)
        game.state.players["A"].life = 6
        self.assertTrue(game.state.players["A"].zones["hand"])

        def require_draw_threshold():
            effects = collect_draw_replacement_effects(game.engine, "A")
            self.assertFalse(any(effect.effect_id.startswith(EMPTY_HAND_DRAW_MULTIPLIER_HANDLER_ID)
                                 for effect in effects))

        def require_life_threshold():
            effects = collect_life_change_replacement_effects(game.engine)
            self.assertFalse(any(effect.effect_id.startswith(LOW_LIFE_GAIN_MULTIPLIER_HANDLER_ID)
                                 for effect in effects))

        require_draw_threshold()
        require_life_threshold()
        with patch("quorune.semantic_runtime.draw_replacements.EMPTY_HAND_DRAW_MULTIPLIER_HANDLER_ID", "ignored"):
            with self.assertRaises(AssertionError):
                require_draw_threshold()
        with patch("quorune.semantic_runtime.life_replacements._LOW_LIFE_GAIN_CEILING", 6):
            with self.assertRaises(AssertionError):
                require_life_threshold()

    def empty_hands(self, game):
        for player in game.state.players.values():
            for identity in tuple(player.zones["hand"]):
                game.engine.move_card(identity, "exile", reason="Set initial fixture hands", log=False)

    def borrowed_phial(self, game):
        phial = self.add(game.engine, "Phial of Galadriel", seat="B", ref="borrowed-phial")
        game.engine.change_control(phial.object_id, "A", reason="Set initial fixture control")
        return phial

    def two_phials(self, game):
        gallery = self.add(game.engine, "Mirror Gallery", ref="phial-legend-exemption")
        phials = tuple(self.add(game.engine, "Phial of Galadriel", ref=f"phial-copy-{i}") for i in (1, 2))
        for card in (gallery, *phials):
            self.assert_closed(game, card)
        return phials

    def choose_replacement(self, game, kind, field, label):
        pending = self.resolve(game)
        self.assertEqual((kind, ["A"]), (pending.kind, pending.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options), options)
        selected, = (option["id"] for option in options if label in option["label"])
        command = {"action_id": "choose", field: selected}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, field: "unoffered"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        return game

    def test_actual_mana_and_empty_hand_draw_two_respect_current_controller(self):
        game = self.session(121614501)
        self.empty_hands(game)
        phial = self.borrowed_phial(game)
        skeins = self.add(game.engine, "Vision Skeins", zone="hand")
        private = self.add(game.engine, "Reach Through Mists", zone="library", ref="phial-private-draw")
        for card in (phial, skeins):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)

        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (action for action in actions if action["id"].startswith(f"activate:{phial.ref}:"))
        paid = game.act("pilot:A", {"action_id": activation["id"], "pay": "auto", "mana_output": {"U": 1}})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool["U"])
        self.assertTrue(game.state.cards[phial.object_id].tapped)

        self.cast_for(game, "A", skeins)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((3, 2, 2, 2), tuple(len(game.state.players[s].zones["hand"]) for s in "ABCD"))
        self.assertEqual(1, sum(event.code == "card.draw.replaced.result_draws" for event in game.state.events))
        self.assertEqual("hand", game.state.cards[private.object_id].zone)
        for seat in "BCD":
            self.assertNotIn(private.ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        self.replay(game, load=True)

    def test_two_actual_phials_replace_single_empty_hand_draw_with_three_cards(self):
        game = self.session(121614502)
        self.empty_hands(game)
        first, _ = self.two_phials(game)
        draw = self.add(game.engine, "Reach Through Mists", zone="hand")
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", draw)
        game = self.choose_replacement(game, "draw.replacement", "choice", first.ref)
        self.assertEqual(3, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(2, sum(event.code == "card.draw.replaced.result_draws" for event in game.state.events))
        self.replay(game, load=True)

    def test_actual_draw_redirect_orders_against_phial_at_the_original_affected_seat(self):
        for index, (first, received) in enumerate((("redirecting-thief", 1), ("ordering-phial", 2))):
            with self.subTest(first=first):
                game = self.session(121614503 + index)
                self.empty_hands(game)
                phial = self.add(game.engine, "Phial of Galadriel", ref="ordering-phial")
                thief = self.add(game.engine, "Notion Thief", seat="B", ref="redirecting-thief")
                draw = self.add(game.engine, "Reach Through Mists", zone="hand")
                for source in (phial, thief, draw):
                    self.assert_closed(game, source)
                self.prepare(game, mana={"U": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", draw)
                game = self.choose_replacement(game, "draw.replacement", "choice", first)
                self.assertEqual((0, received), tuple(len(game.state.players[s].zones["hand"]) for s in "AB"))
                self.replay(game, load=True)

    def test_actual_life_gain_doubles_at_five_and_keeps_six_and_other_seats(self):
        cases = (("A", 5, 19), ("A", 6, 13), ("B", 5, 12))
        for index, (recipient, before, after) in enumerate(cases):
            with self.subTest(recipient=recipient, before=before):
                game = self.session(119614507 + index)
                phial = self.borrowed_phial(game)
                gain = self.add(game.engine, "Heroes' Reunion", zone="hand")
                for card in (phial, gain):
                    self.assert_closed(game, card)
                game.state.players[recipient].life = before
                self.prepare(game, mana={"G": 1, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", gain, [recipient])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(after, game.state.players[recipient].life)
                self.replay(game, load=True)

    def test_two_actual_low_life_multipliers_keep_the_pre_event_total_for_a_setting(self):
        game = self.session(119614510)
        first, _ = self.two_phials(game)
        wind = self.add(game.engine, "Blessed Wind", zone="hand")
        self.assert_closed(game, wind)
        game.state.players["A"].life = 3
        self.prepare(game, mana={"C": 8, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", wind, ["A"])
        game = self.choose_replacement(game, "replacement.order", "replacement", first.ref)
        self.assertEqual(3 + (20 - 3) * 4, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_life_conversion_order_respects_the_direction_of_the_pending_event(self):
        for index, (first, total) in enumerate((("loss-converter", 4), ("life-doubler", 3))):
            with self.subTest(first=first):
                game = self.session(119614511 + index)
                phial = self.add(game.engine, "Phial of Galadriel", ref="life-doubler")
                remedy = self.add(game.engine, "Tainted Remedy", seat="B", ref="loss-converter")
                gain = self.add(game.engine, "Generic Fixed One-Life Gain", zone="hand")
                for card in (phial, remedy, gain):
                    self.assert_closed(game, card)
                game.state.players["A"].life = 5
                self.prepare(game, mana={"W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", gain)
                game = self.choose_replacement(game, "replacement.order", "replacement", first)
                self.assertEqual(total, game.state.players["A"].life)
                self.replay(game, load=True)
