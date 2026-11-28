"""An actual main-phase branch reads the revalidated graveyard card type."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.graveyard_exile_branches_model import GRAVEYARD_EXILE_BRANCH_OPERATION, GRAVEYARD_EXILE_LAND_MANA_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.resolution_mana_model import ResolutionManaChoiceSpec
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_scheduled_event_pairs as scheduled


class GraveyardLandManaBranchTests(InheritedNativeFixture, unittest.TestCase):
    step = scheduled.ScheduledPairNativeTests.step

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "graveyard-land-mana-branch.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "actual-card-play-and-fixed-cleanup-limit-cards.json",
            fixtures / "graveyard-exile-branch-cards.json",
            fixtures / "mana-value-destruction-cards.json",
            fixtures / "unpreventable-damage-cards.json",
            fixtures / "graveyard-land-mana-branch-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Graveyard result with sealed mana colors", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def _land_game_at_mana_choice(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Klothys, God of Destiny")
        land = self.add(game.engine, "Tree of Tales", seat="D", zone="graveyard")
        self.assert_closed(game, source)
        self.prepare(game)
        self.step(game, "A", "precombat_main", "main")
        self.target(game, "A", [land.ref])
        choice = self.resolve(game)
        self.assertIsNotNone(choice, "The classified land must produce its mana choice")
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        return game, land

    def test_actual_land_classification_omission_is_killed(self):
        from quorune.rules import graveyard_exile_branches

        ordinary = graveyard_exile_branches.commit_graveyard_exile_card_types
        with patch.object(graveyard_exile_branches, "commit_graveyard_exile_card_types",
                          side_effect=lambda *args: ordinary(*args) - {"land"}):
            with self.assertRaisesRegex(AssertionError, "classified land must produce its mana choice"):
                self._land_game_at_mana_choice(60010610)

    def test_actual_artifact_land_exiles_then_offers_only_green_or_red_and_replays(self):
        for index, color in enumerate("GR"):
            with self.subTest(color=color):
                game, land = self._land_game_at_mana_choice(60010601 + index)
                self.assertEqual("exile", game.state.cards[land.object_id].zone)
                game = self.replay(game, load=True)
                before = authoritative_state_hash(game.state)
                for seat, value in (("A", "W"), ("B", color)):
                    rejected = game.act("pilot:" + seat, {"action_id": "choose", "choice": value})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                result = game.act("pilot:A", {"action_id": "choose", "choice": color})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(1, game.state.players["A"].mana_pool[color])
                self.assertEqual(1, sum(game.state.players["A"].mana_pool.values()))
                self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)

    def test_actual_nonland_deals_damage_after_source_departure_and_replays(self):
        game = self.session(60010603)
        source = self.add(game.engine, "Klothys, God of Destiny")
        other = self.add(game.engine, "Grizzly Bears", seat="D", zone="graveyard")
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        for index in range(2):
            self.add(game.engine, "Island", seat="B", ref=f"klothys-removal-island-{index}")
        self.assert_closed(game, source)
        self.prepare(game)
        self.step(game, "A", "precombat_main", "main")
        self.target(game, "A", [other.ref])
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[other.object_id].zone)
        self.assertEqual([42, 38, 38, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_closed_palette_and_exact_branch_authority_fail_closed(self):
        spec = ResolutionManaChoiceSpec("one_color", 1, ("G", "R"))
        effect = spec.effect()
        self.assertEqual(spec, ResolutionManaChoiceSpec.from_effect(effect))
        for changed in ({**effect, "schema_version": 2}, {**effect, "colors": ["G", "G"]},
                        {**effect, "colors": ["R", "G"]}, {**effect, "amount": 2}):
            with self.assertRaises(ValueError):
                ResolutionManaChoiceSpec.from_effect(changed)
        report, registry = register_card(self.db, self.registry, "Klothys, God of Destiny")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = [row for row in registry.programs_for_oracle(report["oracle_id"])
                    if row.effects and row.effects[0].get("op") == GRAVEYARD_EXILE_BRANCH_OPERATION]
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != GRAVEYARD_EXILE_LAND_MANA_CAPABILITY)
        reduced = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        raw = dict(program.effects[0])
        crossed = replace(program, effects=({**raw, "result": {**raw["result"], "schema_version": 1}},))
        for candidate in (reduced, crossed):
            result = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"], result)
