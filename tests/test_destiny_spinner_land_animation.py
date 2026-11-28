"""Native enchantment-count land animation on an ordinary paid activation."""

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
from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.resolution_characteristic_model import COUNTED_TARGET_LAND_ANIMATION_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement


NAME = "Destiny Spinner"
PROFILE = "commander_review"


class DestinySpinnerLandAnimationTests(InheritedNativeFixture, unittest.TestCase):
    activate = movement.FixedOneCounterMovementTests.activate
    reject = movement.FixedOneCounterMovementTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "destiny-spinner.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "same-entrant-land-advantage-cards.json",
            fixtures / "destiny-spinner-land-animation-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Destiny Spinner", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_printed_result_requires_its_target_quantity_and_price(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        activate, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                     if COUNTED_TARGET_LAND_ANIMATION_CAPABILITY in program.capability_dependencies)
        self.assertEqual("you", activate.target_schema["controller_relation"])
        self.assertEqual(("land",), tuple(activate.target_schema["types_any"]))
        modified_price = {**activate.cost_schema, "mana": {**activate.cost_schema["mana"], "G": 2}}
        altered = replace(activate, cost_schema=modified_price)
        self.assertFalse(bind_semantic_program_runtime(
            altered, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        for before, after in (("enchantments", "artifacts"), ("until end of turn", "until your next turn"),
                              ("Target land you control", "Target land"), ("trample and haste", "trample")):
            with self.subTest(after=after):
                copy = replace(card, oracle_text=card.oracle_text.replace(before, after))
                ir = compile_oracle_card(copy, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def test_actual_land_animation_captures_its_count_at_resolution_and_replays(self):
        game = self.session(47839601)
        source = self.add(game.engine, NAME, ref="animation-source")
        first = self.add(game.engine, NAME, ref="removed-before-animation")
        second = self.add(game.engine, NAME, ref="removed-after-animation")
        phased = self.add(game.engine, NAME, ref="phased-enchantment")
        phased.phased_out = True
        self.add(game.engine, NAME, ref="foreign-enchantment", seat="B")
        land = self.add(game.engine, "Forest", ref="animated-tapped-land")
        land.tapped = True
        foreign_land = self.add(game.engine, "Forest", seat="B", ref="wrong-controller-land")
        self.add(game.engine, "Forest", seat="B", ref="b-cost-forest")
        self.add(game.engine, "Forest", ref="a-cost-forest-one")
        self.add(game.engine, "Forest", ref="a-cost-forest-two")
        before = self.add(game.engine, "Naturalize", seat="B", zone="hand", ref="prior-enchantment-removal")
        after = self.add(game.engine, "Naturalize", zone="hand", ref="later-enchantment-removal")
        for card in (source, before, after):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A", mana={"C": 3, "G": 1})
        self.checkpoint(game)
        command = {"action_id": f"activate:{source.ref}:ab2", "pay": "auto"}
        for candidate in (foreign_land, source):
            self.reject(game, "A", {**command, "targets": [candidate.ref]})
        self.activate(game, source, "ab2", [land.ref])
        self.assertTrue(all(not amount for amount in game.state.players["A"].mana_pool.values()))
        self.cast(game, "B", before, [first.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[first.object_id].zone)
        concrete = game.state.cards[land.object_id]
        self.assertTrue(concrete.tapped)
        data = game.engine._effective_card_data(concrete)
        self.assertEqual(({"land", "creature"}, {"forest", "elemental"}, {"basic"}), game.engine._type_parts(data["type_line"]))
        self.assertTrue({"Trample", "Haste"} <= set(data["keywords"]))
        self.assertEqual((2, 2), (game.engine._numeric_stat(land.object_id, "power"), game.engine._numeric_stat(land.object_id, "toughness")))
        self.assertNotIn(after.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.cast(game, "A", after, [second.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[second.object_id].zone)
        self.assertEqual((2, 2), (game.engine._numeric_stat(land.object_id, "power"), game.engine._numeric_stat(land.object_id, "toughness")))
        game = self.replay(game, load=True)
        expire_end_of_turn_continuous_effects(game.state)
        data = game.engine._effective_card_data(game.state.cards[land.object_id])
        self.assertEqual(({"land"}, {"forest"}, {"basic"}), game.engine._type_parts(data["type_line"]))

    def test_animation_replay_witness_detects_a_fixed_count_mutation(self):
        with patch("quorune.live_battlefield_quantities.current_live_battlefield_count", return_value=1):
            with self.assertRaisesRegex(AssertionError, r"\(2, 2\) != \(1, 1\)"):
                self.test_actual_land_animation_captures_its_count_at_resolution_and_replays()
