"""An original controlled entrant compares its public rules names at both boundaries."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.entrant_name_absence import (
    ENTRANT_NAME_ABSENCE_CAPABILITY, ENTRANT_NAME_ABSENCE_CONTEXT, entrant_name_absence_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class EntrantNameAbsenceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entrant-name-absence.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/original-entry-name-absence-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Original controlled entrant names", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def draw_history(self, game):
        return len(game.state.players["A"].draw_history)

    def test_actual_same_name_battlefield_and_graveyard_prevent_placement_for_their_controller(self):
        for index, (seat, zone) in enumerate((("A", "battlefield"), ("A", "graveyard"), ("B", "battlefield"), ("B", "graveyard"))):
            with self.subTest(seat=seat, zone=zone):
                game = self.session(76221101 + index)
                source = self.add(game.engine, "Guardian Project")
                entrant = self.add(game.engine, "Grizzly Bears", zone="hand")
                self.add(game.engine, "Grizzly Bears", seat=seat, zone=zone, ref="already-public-bear")
                initial = self.draw_history(game)
                self.start(game, source, entrant, mana={"G": 1, "C": 1})
                self.cast(game, "A", entrant)
                self.resolve_top(game)
                self.assertEqual(int(seat == "B"), self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(initial + int(seat == "B"), self.draw_history(game))
                self.replay(game, load=True)

    def test_actual_blinked_copy_blocks_the_old_trigger_and_draws_for_its_own_entry(self):
        game = self.session(76221110)
        source = self.add(game.engine, "Guardian Project")
        entrant = self.add(game.engine, "Grizzly Bears", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Plains")
        initial = self.draw_history(game)
        self.start(game, source, entrant, blink, mana={"G": 1, "C": 1})
        self.cast(game, "A", entrant)
        self.resolve_top(game)
        original = game.state.cards[entrant.object_id]
        original_incarnation = original.logical_object_id
        old_trigger = game.state.stack[-1].ref
        self.cast(game, "A", blink, [original.ref])
        self.resolve_top(game)
        self.assertNotEqual(original_incarnation, game.state.cards[entrant.object_id].logical_object_id)
        self.assertEqual(2, self.trigger_count(game, source))
        old = next(item for item in game.state.stack if item.ref == old_trigger)
        self.assertEqual({"logical_object_id": original_incarnation, "names": ["Grizzly Bears"]},
                         old.context[ENTRANT_NAME_ABSENCE_CONTEXT])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(initial + 1, self.draw_history(game))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial + 1, self.draw_history(game))
        self.replay(game, load=True)

    def test_actual_departing_entrant_counts_as_a_new_card_in_the_controllers_graveyard(self):
        game = self.session(76221111)
        source = self.add(game.engine, "Guardian Project")
        entrant = self.add(game.engine, "Grizzly Bears", zone="hand")
        doom = self.add(game.engine, "Doom Blade", seat="B", zone="hand")
        self.add(game.engine, "Swamp", seat="B")
        self.add(game.engine, "Plains", seat="B", ref="opponent-generic-land")
        initial = self.draw_history(game)
        self.start(game, source, entrant, doom, mana={"G": 1, "C": 1})
        self.cast(game, "A", entrant)
        self.resolve_top(game)
        original = game.state.cards[entrant.object_id]
        self.cast(game, "B", doom, [original.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[entrant.object_id].zone)
        self.assertEqual(["Grizzly Bears"], game.state.stack[-1].context[ENTRANT_NAME_ABSENCE_CONTEXT]["names"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial, self.draw_history(game))
        self.replay(game, load=True)

    def test_actual_nameless_entries_draw_twice_and_reveal_neither_printed_nor_drawn_name(self):
        game = self.session(76221112)
        source = self.add(game.engine, "Guardian Project")
        morphs = [self.add(game.engine, "Scornful Egotist", zone="hand", ref=f"hidden-entrant-{index}") for index in (1, 2)]
        draws = [game.state.cards[object_id].ref for object_id in game.state.players["A"].zones["library"][-2:]]
        initial = self.draw_history(game)
        self.start(game, source, *morphs, mana={"C": 6})
        for expected, card in enumerate(morphs, start=1):
            self.priority(game, "A")
            actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            action, = (row for row in actions if row["id"] == "cast-morph:" + card.ref)
            result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.cards[card.object_id].face_down)
            self.assertEqual(initial + expected, self.draw_history(game))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn("Scornful Egotist", packet)
            for reference in draws:
                self.assertNotIn(reference, packet)
        self.replay(game, load=True)

    def test_full_source_requires_both_public_name_scopes_and_independent_authority(self):
        card = self.db.lookup("Guardian Project", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Guardian Project")
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn(ENTRANT_NAME_ABSENCE_CAPABILITY, entrant_name_absence_runtime_dependencies(program))
        deps = tuple(cap for cap in program.capability_dependencies if cap != ENTRANT_NAME_ABSENCE_CAPABILITY)
        altered = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{ENTRANT_NAME_ABSENCE_CAPABILITY}", binding["blockers"])
        for old, new in (
            ("a nontoken creature", "a creature"), ("your graveyard", "an opponent's graveyard"),
            ("draw a card.", "draw two cards."), ("draw a card.", "draw a card. Change the name of each card in your hand."),
        ):
            altered_card = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                altered_card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.public_trigger_conditions._entrant_name_absence_binding", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
