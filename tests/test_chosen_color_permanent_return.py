"""A resolved color selects one live public permanent return batch."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import PublicZoneMoveSetSpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Wash Out"
PROFILE = "commander_review"
COLORS = ("W", "U", "B", "R", "G")


class ChosenColorPermanentReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-color-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/chosen-color-permanent-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen color permanent return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return card, program

    def test_complete_wash_out_has_exactly_the_five_untargeted_public_move_branches(self):
        card, program = self.program()
        self.assertIsNone(program.target_schema)
        choice, = program.effects
        self.assertEqual(("choose_option", "$controller"), (choice["op"], choice["player"]))
        self.assertEqual(COLORS, tuple(entry["id"] for entry in choice["options"]))
        for color, effects in choice["then_by_choice"].items():
            effect, = effects
            self.assertEqual(("move_public_zone_set", "$source"), (effect["op"], effect["source"]))
            spec = PublicZoneMoveSetSpec.from_dict(effect["set"])
            self.assertEqual(("battlefield", "hand", "any"), (spec.origin.value, spec.destination.value, spec.seat_relation.value))
            self.assertEqual((color,), spec.query.colors_any)
            self.assertEqual((), spec.query.types_all)
        for text in (
            card.oracle_text.replace("permanents", "creatures"),
            card.oracle_text.replace("their owners' hands", "their owners' libraries"),
            card.oracle_text.replace("Return", "Target player returns"),
        ):
            changed = replace(card, oracle_text=text)
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)

    def test_raw_color_branches_reject_missing_or_mismatched_move_sets_and_wrong_actor(self):
        _, program = self.program()
        choice, = program.effects
        branches = choice["then_by_choice"]
        for payload in (
            {**choice, "player": "$target.0"},
            {**choice, "then_by_choice": {**branches, "B": branches["G"]}},
            {**choice, "then_by_choice": {color: branch for color, branch in branches.items() if color != "B"}},
            {**choice, "options": [*choice["options"], {"id": "C", "label": "Colorless"}]},
        ):
            result = bind_semantic_program_runtime(
                replace(program, effects=(payload,)), capability_registry=self.registry, profile=PROFILE,
            )
            self.assertFalse(result["strict"], result)

    def color_offer(self, game):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        schema = packet["legal_actions"][0]["choice_schema"]
        self.assertEqual(set(COLORS), set(schema["legal_values"]))

    def test_actual_response_changes_the_live_set_and_the_return_reaches_all_owners_atomically(self):
        game = self.session(10540031)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        donate = self.add(engine, "Donate", zone="hand")
        borrowed = self.add(engine, "Slippery Bogle")
        bear = self.add(engine, "Grizzly Bears", seat="B")
        lion = self.add(engine, "Savannah Lions", seat="C")
        arena = self.add(engine, "Phyrexian Arena", seat="B")
        hour = self.add(engine, "Darkest Hour", seat="B", zone="hand")
        timing = self.add(engine, "Vedalken Orrery", seat="B")
        swamp = self.add(engine, "Swamp", seat="B")
        ring = self.add(engine, "Sol Ring", seat="C")
        red = self.add(engine, "Fervor", seat="D")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="wash-other-hand")
        for card in (source, donate, borrowed, hour, timing):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", donate, ["B", borrowed.ref], reserve={"C": 3, "U": 1})
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (game.state.cards[borrowed.object_id].owner, game.state.cards[borrowed.object_id].controller))
        self.assertNotIn("B", engine._effective_card_data(game.state.cards[bear.object_id])["colors"])

        self.cast_for(game, "A", source)
        self.cast_for(game, "B", hour)
        self.resolve_top(game)
        self.assertEqual(["B"], engine._effective_card_data(game.state.cards[bear.object_id])["colors"])
        self.color_offer(game)
        before = authoritative_state_hash(game.state)
        for seat, color in (("B", "B"), ("A", "C"), ("A", "BU")):
            result = game.act("pilot:" + seat, {"action_id": "choose", "choice": color})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "B"})
        self.assertTrue(result.ok, result.summary)
        for returned in (borrowed, bear, lion, arena, hour):
            current = game.state.cards[returned.object_id]
            self.assertIn(current.object_id, game.state.players[returned.owner].zones["hand"])
        for untouched in (timing, swamp, ring, red):
            self.assertEqual("battlefield", game.state.cards[untouched.object_id].zone)
        self.assertFalse(game.state.stack)
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_empty_color_choice_is_available_and_completes_the_spell(self):
        game = self.session(10540032)
        source = self.add(game.engine, NAME, zone="hand")
        ring = self.add(game.engine, "Sol Ring")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.color_offer(game)
        result = game.act("pilot:A", {"action_id": "choose", "choice": "W"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[ring.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)

    def test_complete_original_witness_kills_omitting_the_color_choice_compiler(self):
        with patch("quorune.compiler.public_zone_move_templates.chosen_color_permanent_return_template", return_value=None):
            with self.assertRaises(AssertionError):
                self.program()
