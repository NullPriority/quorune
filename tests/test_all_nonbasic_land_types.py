"""Actual land play, live subtype evaluation and mana replay for Planar Nexus."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.basic_land_types import ALL_NONBASIC_LAND_TYPES_CAPABILITY, BASIC_LAND_MANA, NONBASIC_LAND_SUBTYPES
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.all_colors import AllNonbasicLandTypesDefinitionHandler
from scripts.build_test_database import build_fixture_database


class AllNonbasicLandTypesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "nonbasic-land-types.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/all-nonbasic-land-types-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("All nonbasic land types", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_definition_uses_current_rules_vocabulary_and_rejects_other_scopes(self):
        record = self.db.lookup("Planar Nexus", fuzzy=False)
        ir = self.compile(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, first, second = ir.faces[0].nodes
        self.assertEqual("all", node.active_zone)
        self.assertIn(ALL_NONBASIC_LAND_TYPES_CAPABILITY, node.capability_dependencies)
        self.assertEqual(("mana_ability", "mana_ability"), (first.kind, second.kind))
        self.assertEqual({
            "cave", "desert", "gate", "lair", "locus", "mine", "planet",
            "power-plant", "sphere", "tower", "town", "urza's",
        }, NONBASIC_LAND_SUBTYPES)
        self.assertFalse(set(BASIC_LAND_MANA).intersection(NONBASIC_LAND_SUBTYPES))
        definition, = node.handlers
        handler = AllNonbasicLandTypesDefinitionHandler()
        handler.validate(definition)
        broken = {**definition, "fragment": {**definition["fragment"], "schema_version": True}}
        with self.assertRaises(ValueError):
            handler.validate(broken)
        for old, new in (("This land", "Each land"), ("every nonbasic land type", "every land type")):
            altered = self.compile(replace(record, oracle_text=record.oracle_text.replace(old, new)))
            self.assertNotEqual("exact", altered.status)

    def type_parts(self, game, card):
        source = game.state.cards[card.object_id]
        data = game.engine._effective_card_data(source)
        return game.engine._type_parts(str(data["type_line"]))

    def mana(self, game, card, ability, **choices):
        decision = game.packet("pilot:A", full=True)["decision"]
        selection, = [row for row in decision["ctx"]["legal"]["actions"]
                      if row["id"] == f"activate:{card.ref}:{ability}"]
        result = game.act("pilot:A", {"action_id": selection["id"], **choices})
        self.assertTrue(result.ok, result.summary)

    def test_native_nexus_funds_tower_and_paid_mana_before_a_type_setting_replays(self):
        game = self.session(746039001)
        nexus = self.add(game.engine, "Planar Nexus", zone="hand")
        tower = self.add(game.engine, "Urza's Tower")
        moon = self.add(game.engine, "Blood Moon", zone="hand")
        for card in (nexus, tower, moon):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.assertEqual(NONBASIC_LAND_SUBTYPES, self.type_parts(game, nexus)[1])
        offer = game.act("pilot:A", {"action_id": f"play-land:{nexus.ref}"})
        self.assertTrue(offer.ok, offer.summary)
        self.assertEqual("battlefield", game.state.cards[nexus.object_id].zone)
        self.assertEqual(({"land"}, NONBASIC_LAND_SUBTYPES, set()), self.type_parts(game, nexus))
        self.mana(game, tower, "ab1")
        self.assertEqual(3, game.state.players["A"].mana_pool["C"])
        self.mana(game, nexus, "ab3", pay="auto", mana_output={"R": 1})
        self.assertEqual((2, 1), tuple(game.state.players["A"].mana_pool[mana] for mana in ("C", "R")))
        game = self.replay(game, load=True)
        self.cast_for(game, "A", moon)
        self.resolve_top(game)
        self.assertEqual(({"land"}, {"mountain"}, set()), self.type_parts(game, nexus))
        self.replay(game, load=True)

    def test_native_ordinary_colorless_activation_replays(self):
        game = self.session(746039002)
        nexus = self.add(game.engine, "Planar Nexus")
        self.assert_closed(game, nexus)
        self.prepare(game)
        self.checkpoint(game)
        self.mana(game, nexus, "ab2")
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual(1, sum(game.state.players["A"].mana_pool.values()))
        self.assertTrue(game.state.cards[nexus.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)
