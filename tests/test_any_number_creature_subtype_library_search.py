"""Private any-number creature searches commit one native entry batch."""

from pathlib import Path
import json
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


ELVES = (
    "Trial Library Elf A", "Trial Library Elf B", "Trial Library Elf C",
    "Trial Library Elf D", "Trial Library Elf E", "Trial Library Elf F",
    "Trial Library Elf G", "Trial Library Elf H", "Trial Library Elf I",
    "Trial Library Elf J", "Trial Library Elf K", "Trial Library Elf L",
)


class AnyNumberCreatureSubtypeLibrarySearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "typed-creature-search.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "any-number-creature-subtype-library-search-cards.json",
            fixtures / "literal-name-library-battlefield-search-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Typed creature library search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def shuffles(self, game):
        return game.state.players["A"].stats.get("shuffle_count", 0)

    def choose(self, game, seat, **choice):
        return game.act("pilot:" + seat, {"action_id": "choose", **choice})

    def begin(self, seed, names, *, replacements=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "Trial Wilds Guide")
        source.counters["loyalty"] = 8
        elves = [self.add(game.engine, name, zone="library", ref=f"private-typed-search-{i}")
                for i, name in enumerate(names)]
        kindred = self.add(game.engine, "Trial Elf Kindred Spell", zone="library", ref="private-kindred-control")
        required = [source, *elves]
        if replacements:
            required += [self.add(game.engine, name) for name in
                         ("Grumgully, the Generous", "Hardened Scales", "Doubling Season")]
        for card in required:
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        before = self.shuffles(game)
        result = game.act("pilot:A", {"action_id": f"activate:{source.ref}:ab1", "targets": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, game.state.cards[source.object_id].counters["loyalty"])
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, list(pending.actors)))
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, len(elves)), (schema["minimum"], schema["maximum"]))
        self.assertEqual({card.ref for card in elves}, set(schema["legal_refs"]))
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in (*elves, kindred):
                self.assertNotIn(card.ref, packet)
        return game, elves, kindred, before

    def test_more_than_ten_creatures_can_enter_and_unselected_kindred_remains_private(self):
        game, elves, kindred, before = self.begin(176114015, ELVES)
        game = self.replay(game, load=True)
        snapshot = authoritative_state_hash(game.state)
        self.assertFalse(self.choose(game, "A", search_cards=[kindred.ref]).ok)
        self.assertFalse(self.choose(game, "B", search_cards=[elves[0].ref]).ok)
        self.assertEqual(snapshot, authoritative_state_hash(game.state))
        result = self.choose(game, "A", search_cards=[card.ref for card in elves])
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in elves))
        self.assertEqual("library", game.state.cards[kindred.object_id].zone)
        self.assertNotIn(kindred.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_two_creatures_resume_the_private_search_after_entry_replacement(self):
        game, elves, kindred, before = self.begin(176114016, ELVES[:2], replacements=True)
        result = self.choose(game, "A", search_cards=[card.ref for card in elves])
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(before, self.shuffles(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in elves))
        self.assertNotIn(kindred.ref, json.dumps(game.packet("pilot:B", full=True)))
        game = self.replay(game, load=True)
        for _ in range(4):
            pending = game.state.pending_decision
            if pending is None or pending.kind != "replacement.order":
                break
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            scales = next(row for row in options if "hardened" in (row["id"] + row["label"]).casefold())
            result = self.choose(game, "A", replacement=scales["id"])
            self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual([4, 4], [game.state.cards[card.object_id].counters.get("+1/+1") for card in elves])
        self.assertEqual("library", game.state.cards[kindred.object_id].zone)
        self.replay(game, load=True)


if __name__ == "__main__":
    unittest.main()
