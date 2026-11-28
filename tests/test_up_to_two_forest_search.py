"""Native counted Forest search, simultaneous entry choices and replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import UP_TO_TWO_FOREST_BATCH_CAPABILITY, FixedCountedLibrarySearchTemplate
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class UpToTwoForestSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "two-forest-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/up-to-two-forest-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Two Forest search", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_forest_selector_has_no_tapped_override(self):
        record = self.db.lookup("Skyshroud Claim", fuzzy=False)
        result = self.compile(record)
        self.assertEqual("exact", result.status, result.material_residuals)
        node, = result.faces[0].nodes
        self.assertIn(UP_TO_TWO_FOREST_BATCH_CAPABILITY, node.capability_dependencies)
        effect, = node.effects
        self.assertEqual({"types": ["land"], "subtypes_any": ["forest"]}, effect["selector"])
        self.assertEqual({"minimum": 0, "maximum": 2}, effect["count"])
        self.assertTrue(FixedCountedLibrarySearchTemplate.from_effect(effect).up_to_two_forest_batch)
        self.assertNotIn("enters_tapped_override", effect)
        for old, new in (("up to two", "up to three"), ("Forest cards", "basic Forest cards"),
                         ("battlefield", "battlefield tapped"), ("Forest cards", "Mountain cards")):
            with self.subTest(changed=old):
                changed = self.compile(replace(record, oracle_text=record.oracle_text.replace(old, new)))
                self.assertFalse(any(UP_TO_TWO_FOREST_BATCH_CAPABILITY in node.capability_dependencies
                                     for node in changed.faces[0].nodes))

    def setup_search(self, seed, *, shocks):
        game = self.session(seed)
        source = self.add(game.engine, "Skyshroud Claim", zone="hand")
        forest = self.add(game.engine, "Forest", zone="library", ref="ordinary-forest")
        wrong = self.add(game.engine, "Mountain", zone="library", ref="excluded-mountain")
        garden = self.add(game.engine, "Temple Garden", zone="library", ref="forest-temple-garden") if shocks else None
        ground = self.add(game.engine, "Stomping Ground", zone="library", ref="forest-stomping-ground") if shocks else None
        for card in (source, garden, ground):
            if card is not None:
                self.assert_closed(game, card)
        if shocks:
            game.state.players["A"].life = 3
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        before = game.state.players["A"].stats.get("shuffle_count", 0)
        self.cast_for(game, "A", source)
        self.assertEqual("semantic.search", self.resolve(game).kind)
        return game, forest, wrong, garden, ground, before

    def search_choice(self, game, refs, *, accepted=True, seat="A"):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": refs})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_two_nonbasic_forests_each_choose_entry_and_replay_one_shuffle(self):
        game, forest, wrong, garden, ground, before = self.setup_search(747042001, shocks=True)
        decision = game.packet("pilot:A", full=True)["decision"]
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 2), (schema["minimum"], schema["maximum"]))
        self.assertEqual({forest.ref, garden.ref, ground.ref}, set(schema["legal_refs"]))
        self.assertNotIn(wrong.ref, schema["legal_refs"])
        self.assertNotIn("entry_pay_life", schema)
        self.search_choice(game, [forest.ref, garden.ref, ground.ref], accepted=False)
        self.search_choice(game, [garden.ref, garden.ref], accepted=False)
        self.search_choice(game, [wrong.ref], accepted=False)
        self.search_choice(game, [garden.ref], accepted=False, seat="B")
        game = self.replay(game, load=True)
        self.search_choice(game, [garden.ref, ground.ref])
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.assertEqual(("pilot:A",), tuple(game.pending_principals()))
        self.assertEqual(3, game.state.players["A"].life)
        self.assertEqual(before, game.state.players["A"].stats.get("shuffle_count", 0))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in (garden, ground)))
        for observer in "BCD":
            packet = game.packet("pilot:" + observer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(garden.ref, json.dumps(packet))
            self.assertNotIn(ground.ref, json.dumps(packet))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(
            {"Pay no life", "Pay 2 life for Temple Garden", "Pay 2 life for Stomping Ground"},
            {row["label"] for row in options},
        )
        before_choice = authoritative_state_hash(game.state)
        denied = game.act("pilot:A", {"action_id": "choose", "choice": "entry-3"})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before_choice, authoritative_state_hash(game.state))
        pay, = [row for row in options if row["label"] == "Pay 2 life for Temple Garden"]
        result = game.act("pilot:A", {"action_id": "choose", "choice": pay["id"]})
        self.assertTrue(result.ok, result.summary)
        paid_index = garden.object_id
        self.assertEqual(1, game.state.players["A"].life)
        self.assertEqual(before + 1, game.state.players["A"].stats["shuffle_count"])
        for card in (garden, ground):
            current = game.state.cards[card.object_id]
            self.assertEqual("battlefield", current.zone)
            self.assertEqual(card.object_id != paid_index, current.tapped)
        self.assertEqual("library", game.state.cards[forest.object_id].zone)
        self.replay(game, load=True)

    def test_actual_zero_and_one_ordinary_forest_keep_optional_count_and_shuffle_once(self):
        for quantity in (0, 1):
            with self.subTest(quantity=quantity):
                game, forest, wrong, _, _, before = self.setup_search(747042010 + quantity, shocks=False)
                self.search_choice(game, [forest.ref] if quantity else [])
                self.assertEqual(before + 1, game.state.players["A"].stats["shuffle_count"])
                current = game.state.cards[forest.object_id]
                self.assertEqual("battlefield" if quantity else "library", current.zone)
                if quantity:
                    self.assertFalse(current.tapped)
                self.assertEqual("library", game.state.cards[wrong.object_id].zone)
                self.replay(game, load=True)
