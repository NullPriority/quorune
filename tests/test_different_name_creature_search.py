"""Native differently named creature selection, public results, and replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import DIFFERENT_NAME_CREATURE_SEARCH_CAPABILITY, FixedCountedLibrarySearchTemplate
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DifferentNameCreatureSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "different-name-creature-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/different-name-creature-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Different-name creature search", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

    def test_exact_different_name_selector_fails_closed(self):
        original = self.db.lookup("Shared Summons", fuzzy=False)
        ir = self.compile(original)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(DIFFERENT_NAME_CREATURE_SEARCH_CAPABILITY, node.capability_dependencies)
        self.assertIsNone(node.target_schema)
        effect, = node.effects
        self.assertEqual({"types": ["creature"]}, effect["selector"])
        self.assertIs(True, effect["different_names"])
        self.assertIs(True, effect["reveal"])
        self.assertIs(True, effect["shuffle_after"])
        self.assertEqual("hand", effect["destination"])
        for fields in ({"different_names": 1}, {"different_names": False}, {"reveal": False},
                       {"selector": {"types": ["land"]}}, {"count": {"minimum": 0, "maximum": 1}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                FixedCountedLibrarySearchTemplate.from_effect({**effect, **fields})
        changed = replace(original, oracle_text=original.oracle_text.replace(" with different names", ""))
        self.assertFalse(any(DIFFERENT_NAME_CREATURE_SEARCH_CAPABILITY in node.capability_dependencies
                             for node in self.compile(changed).faces[0].nodes))

    def setup_search(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Shared Summons", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", zone="library", ref="first-copy-bears")
        second = self.add(game.engine, "Grizzly Bears", zone="library", ref="second-copy-bears")
        other = self.add(game.engine, "Llanowar Elves", zone="library", ref="distinct-elf")
        wrong = self.add(game.engine, "Plains", zone="library", ref="not-a-creature")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "G": 2})
        self.checkpoint(game)
        count = game.state.players["A"].stats.get("shuffle_count", 0)
        self.cast_for(game, "A", source)
        self.assertEqual("semantic.search", self.resolve(game).kind)
        return game, first, second, other, wrong, count

    def select(self, game, cards, *, accepted=True, seat="A"):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", {"action_id": "choose", "search_cards": [card.ref for card in cards]})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))
        return result

    def public_result(self, game):
        result = next(event for event in reversed(game.state.events) if event.code == "library.search")
        self.assertIs(True, result.details["revealed"])
        return result.details

    def test_actual_search_rejects_two_copies_and_reveals_only_distinct_selected_cards(self):
        game, first, second, other, wrong, before = self.setup_search(747044001)
        schema = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 2), (schema["minimum"], schema["maximum"]))
        self.assertEqual({first.ref, second.ref, other.ref}, set(schema["legal_refs"]))
        for observer in "BCD":
            packet = game.packet("pilot:" + observer, full=True)
            self.assertIsNone(packet["decision"])
            for card in (first, second, other, wrong):
                self.assertNotIn(card.ref, json.dumps(packet))
        declined = self.select(game, [first, second], accepted=False)
        self.assertIn("different names", declined.summary)
        self.select(game, [wrong], accepted=False)
        self.select(game, [other], accepted=False, seat="B")
        self.assertEqual(before, game.state.players["A"].stats.get("shuffle_count", 0))
        game = self.replay(game, load=True)
        self.select(game, [second, other])
        self.assertEqual(before + 1, game.state.players["A"].stats["shuffle_count"])
        self.assertEqual({second.ref, other.ref}, set(self.public_result(game)["objects"]))
        for card in (second, other):
            self.assertEqual("hand", game.state.cards[card.object_id].zone)
        for card in (first, wrong):
            self.assertEqual("library", game.state.cards[card.object_id].zone)
        self.assertNotIn(first.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_zero_and_one_replay_partial_search_and_one_shuffle(self):
        for count in (0, 1):
            with self.subTest(count=count):
                game, first, second, other, _, before = self.setup_search(747044010 + count)
                self.select(game, [first] if count else [])
                self.assertEqual(before + 1, game.state.players["A"].stats["shuffle_count"])
                self.assertEqual(count, self.public_result(game)["count"])
                self.assertEqual("hand" if count else "library", game.state.cards[first.object_id].zone)
                self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in (second, other)))
                self.replay(game, load=True)
