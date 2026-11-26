"""A source's linked entry color funds immediate mana for the actual land controller."""

from dataclasses import replace
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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.triggered_mana_model import (
    SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY, fixed_triggered_mana_runtime_dependencies, fixed_triggered_mana_spec,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class SourceChosenTriggeredManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-chosen-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-chosen-tap-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Source-chosen tap mana", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def choose_color(self, game, color):
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        choice, = (row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                   if row["id"].endswith(":" + color))
        command = {"action_id": "choose", "choices": {"replacement": choice}}
        initial = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def mana_action(self, game, card, seat="B"):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row.get("source") == game.state.cards[card.object_id].ref)
        return {"action_id": action["id"]}

    def activate_land(self, game, card, bundle):
        command = {**self.mana_action(game, card), "mana_output": bundle}
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)
        self.assertFalse(game.state.pending_trigger_batches)

    def test_actual_aura_funds_the_foreign_forest_controllers_immediate_cast(self):
        for direct in (True, False):
            with self.subTest(direct=direct):
                game = self.session(76231201 + direct)
                aura = self.add(game.engine, "Utopia Sprawl", zone="hand")
                forest = self.add(game.engine, "Forest", seat="B", ref="foreign-aura-forest")
                plain = self.add(game.engine, "Forest", seat="B", ref="untouched-foreign-forest")
                spell = self.add(game.engine, "Opt", seat="B", zone="hand")
                self.start(game, aura, spell, mana={"G": 1})
                self.cast(game, "A", aura, [forest.ref])
                game = self.replay(game, load=True)
                self.choose_color(game, "U")
                command = self.mana_action(game, forest)
                initial = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", {**command, "mana_output": {"U": 1, "G": 1}}).ok)
                self.assertEqual(initial, authoritative_state_hash(game.state))
                if direct:
                    self.activate_land(game, forest, {"G": 1})
                    self.assertEqual(1, game.state.players["B"].mana_pool["U"])
                self.cast(game, "B", spell)
                self.assertEqual(["spell"], [item.kind for item in game.state.stack])
                self.assertEqual((1, 0), tuple(game.state.players["B"].mana_pool[color] for color in "GU"))
                self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                self.assertTrue(game.state.cards[forest.object_id].tapped)
                self.assertFalse(game.state.cards[plain.object_id].tapped)
                event, = (row for row in game.state.events if row.code == "mana.triggered")
                self.assertEqual({"U": 1}, event.details["bundle"])
                self.assertEqual(game.state.cards[aura.object_id].ref, event.details["source"])
                self.replay(game, load=True)

    def test_actual_gauntlet_requires_basic_land_and_the_real_produced_color_for_every_controller(self):
        game = self.session(76231204)
        gauntlet = self.add(game.engine, "Gauntlet of Power", zone="hand")
        mountain = self.add(game.engine, "Mountain", seat="B")
        furnace = self.add(game.engine, "Great Furnace", seat="B")
        island = self.add(game.engine, "Island", seat="B")
        goblin = self.add(game.engine, "Raging Goblin", seat="B")
        self.start(game, gauntlet, mana={"C": 5})
        self.cast(game, "A", gauntlet)
        self.choose_color(game, "R")
        self.assertEqual(2, game.engine._numeric_stat(goblin.object_id, "power"))
        self.activate_land(game, furnace, {"R": 1})
        self.assertEqual(1, game.state.players["B"].mana_pool["R"])
        self.activate_land(game, island, {"U": 1})
        self.assertEqual(1, game.state.players["B"].mana_pool["U"])
        game = self.replay(game, load=True)
        self.activate_land(game, mountain, {"R": 1})
        self.assertEqual(3, game.state.players["B"].mana_pool["R"])
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        event, = (row for row in game.state.events if row.code == "mana.triggered")
        self.assertEqual((mountain.ref, {"R": 1}), (event.details["producer"], event.details["bundle"]))
        self.replay(game, load=True)

    def test_actual_gauntlet_without_linked_entry_choice_produces_no_bonus(self):
        game = self.session(76231205)
        gauntlet = self.add(game.engine, "Gauntlet of Power")
        mountain = self.add(game.engine, "Mountain", seat="B")
        self.start(game, gauntlet, mana={})
        self.activate_land(game, mountain, {"R": 1})
        self.assertEqual(1, game.state.players["B"].mana_pool["R"])
        self.assertFalse(any(row.code == "mana.triggered" for row in game.state.events))
        self.replay(game, load=True)

    def test_complete_originals_require_linked_entry_and_independent_mana_authority(self):
        for report, semantics in (
            register_card(self.db, self.registry, "Utopia Sprawl"),
            register_card(self.db, self.registry, "Gauntlet of Power"),
        ):
            with self.subTest(oracle_id=report["oracle_id"]):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (program for program in semantics.programs_for_oracle(report["oracle_id"])
                            if SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY in program.capability_dependencies)
                self.assertIn(SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY, fixed_triggered_mana_runtime_dependencies(program))
                self.assertEqual(3, fixed_triggered_mana_spec(program).to_dict()["schema_version"])
                deps = ("mana.triggered.fixed_output",)
                absent = replace(program, capability_dependencies=deps,
                                 capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn(
                    f"capability:undeclared_runtime_dependency:{SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY}", binding["blockers"],
                )
        card = self.db.lookup("Utopia Sprawl", fuzzy=False)
        for old, new in (
            ("As this Aura enters, choose a color.\n", ""), ("enchanted Forest", "enchanted artifact"),
            ("one mana of the chosen color", "two mana of the chosen color"),
            ("one mana of the chosen color.", "one mana of the chosen color. Draw a card."),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        with patch("quorune.compiler.triggered_mana_nodes._source_chosen_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
