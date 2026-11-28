"""Native Burning-Rune Demon search, reveal, opponent choice and replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_library_piles_model import (
    PILE_OPERATION, PILE_SILVER_VERSION, SEARCHED_PAIR_PILE_VERSION, PublicLibraryPileSpec,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.searched_library_pairs import (
    SEARCHED_PAIR_CAPABILITY, searched_pair_search, validate_searched_pair_search,
)


DEMON = "Burning-Rune Demon"


class BurningRuneDemonTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "searched-library-pair.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/searched-library-pair-cards.json",
            ROOT / "tests/fixtures/hand-library-redraw-cards.json",
            ROOT / "tests/fixtures/library-search-rules-cards.json",
            ROOT / "tests/fixtures/graveyard-exile-replacement-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Searched library pair",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def typed_ir(self, record):
        return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

    def submit(self, game, actor, *, accept=True, **payload):
        before = authoritative_state_hash(game.state)
        outcome = game.act(f"pilot:{actor}", {"action_id": "choose", **payload})
        self.assertEqual(accept, outcome.ok, outcome.summary)
        if not accept:
            self.assertEqual(before, authoritative_state_hash(game.state))
        return outcome

    def announce(self, game, source):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 4, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["A"], list(game.state.pending_decision.actors))
        return self.shuffles(game)

    @staticmethod
    def shuffles(game):
        return game.state.players["A"].stats.get("shuffle_count", 0)

    def start(self, game):
        self.submit(game, "A", choice="apply")
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.search", decision["kind"])
        return decision["legal_actions"][0]["choice_schema"]

    def scene(self, seed, *, replace_graveyard=False):
        game = self.session(seed)
        source = self.add(game.engine, "Burning-Rune Demon", zone="hand")
        first = self.add(game.engine, "Valakut Awakening // Valakut Stoneforge", zone="library", ref="pair-first-dfc")
        second = self.add(game.engine, "Grizzly Bears", zone="library", ref="pair-other-card")
        duplicate = self.add(game.engine, "Valakut Awakening // Valakut Stoneforge", zone="library", ref="pair-second-dfc")
        excluded = self.add(game.engine, "Burning-Rune Demon", zone="library", ref="pair-excluded-name")
        hidden = self.add(game.engine, "Swamp", zone="library", ref="pair-private-library-top")
        if replace_graveyard:
            for seat in "BD":
                self.add(game.engine, "Rest in Peace", seat=seat, ref=f"pair-replacement-{seat}")
        before = self.announce(game, source)
        return game, first, second, duplicate, excluded, hidden, before

    def test_demon_exact_pair_closure_and_scoped_descriptor(self):
        record = self.db.lookup(DEMON, fuzzy=False)
        ir = self.typed_ir(record)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertFalse(ir.material_residuals)
        nodes = [node for node in ir.faces[0].nodes if node.event == "permanent.enter.self"]
        trigger, = nodes
        self.assertIn(SEARCHED_PAIR_CAPABILITY, trigger.capability_dependencies)
        self.assertIsNone(trigger.target_schema)
        effect = searched_pair_search(DEMON)
        self.assertEqual(DEMON, validate_searched_pair_search(effect))
        for mutation in ({"shuffle_after": True}, {"destination": "hand"}, {"count": {"minimum": 1, "maximum": 2}}):
            with self.assertRaises(ValueError):
                validate_searched_pair_search({**effect, **mutation})
        changed = replace(record, oracle_text=record.oracle_text.replace("exactly two", "up to two"))
        self.assertNotEqual("exact", self.typed_ir(changed).status)
        searched = {"op": PILE_OPERATION, "player": "$controller", "schema_version": SEARCHED_PAIR_PILE_VERSION,
                    "look_count": 2, "opponent_role": "chooser"}
        with self.assertRaises(ValueError):
            PublicLibraryPileSpec.from_effect(searched)
        silver = PublicLibraryPileSpec.from_effect({**searched, "schema_version": PILE_SILVER_VERSION})
        self.assertTrue(silver.silver_split)
        self.assertFalse(silver.searched_pair)
        self.assertFalse(PublicLibraryPileSpec.from_effect({**searched, "schema_version": 1, "look_count": 5}).searched_pair)

    def test_demon_searches_privately_then_opponent_selects_publicly_and_shuffles(self):
        game, first, second, duplicate, excluded, hidden, before = self.scene(91271101)
        initial = game.packet("pilot:A", full=True)["decision"]
        self.assertNotIn(hidden.ref, json.dumps(initial))
        schema = self.start(game)
        self.assertEqual([0, 2], schema["allowed_counts"])
        self.assertNotIn(excluded.ref, schema["legal_refs"])
        for observer in "BCD":
            packet = game.packet(f"pilot:{observer}", full=True)
            self.assertIsNone(packet["decision"])
            for card in (first, second, duplicate, hidden):
                self.assertNotIn(card.ref, json.dumps(packet))
        self.submit(game, "B", accept=False, search_cards=[first.ref, second.ref])
        self.submit(game, "A", accept=False, search_cards=[first.ref])
        self.submit(game, "A", accept=False, search_cards=[first.ref, duplicate.ref])
        self.submit(game, "A", accept=False, search_cards=[first.ref, excluded.ref])
        self.assertEqual(before, self.shuffles(game))
        game = self.replay(game, load=True)
        self.submit(game, "A", search_cards=[first.ref, second.ref])
        self.assertEqual(["A"], list(game.state.pending_decision.actors))
        self.assertEqual("choose_opponent", game.packet("pilot:A", full=True)["decision"]["ctx"]["stage"])
        self.assertEqual(before, self.shuffles(game))
        for observer in "BCD":
            packet = json.dumps(game.packet(f"pilot:{observer}", full=True))
            self.assertIn(first.printed_name, packet)
            self.assertIn(second.printed_name, packet)
            self.assertNotIn(duplicate.ref, packet)
            self.assertNotIn(hidden.ref, packet)
        self.submit(game, "A", accept=False, opponent="A")
        self.submit(game, "A", opponent="C")
        self.assertEqual("choose_pile", game.packet("pilot:C", full=True)["decision"]["ctx"]["stage"])
        self.submit(game, "A", accept=False, pile="second")
        game = self.replay(game, load=True)
        first_id = game.state.cards[first.object_id].logical_object_id
        second_id = game.state.cards[second.object_id].logical_object_id
        self.submit(game, "C", pile="second")
        self.assertEqual(before + 1, self.shuffles(game))
        actual_first, actual_second = (game.state.cards[card.object_id] for card in (first, second))
        self.assertEqual(("graveyard", "hand"), (actual_first.zone, actual_second.zone))
        self.assertNotEqual(first_id, actual_first.logical_object_id)
        self.assertNotEqual(second_id, actual_second.logical_object_id)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in (duplicate, excluded, hidden)))
        self.replay(game, load=True)

    def test_demon_decline_and_failed_search_have_different_shuffle_results(self):
        for proceed in (False, True):
            with self.subTest(proceed=proceed):
                game, first, second, duplicate, _, _, before = self.scene(91271102 + proceed)
                start = game.state.event_sequence
                if proceed:
                    self.start(game)
                    self.submit(game, "A", search_cards=[])
                else:
                    self.submit(game, "A", choice="decline")
                self.assertEqual(before + int(proceed), self.shuffles(game))
                self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in (first, second, duplicate)))
                later = [event.code for event in game.state.events if event.event_id > start]
                self.assertNotIn("library.reveal", later)
                self.assertNotIn("library.search.reveal", later)
                self.assertEqual(proceed, "library.search" in later)
                self.replay(game, load=True)

    def test_demon_honors_the_existing_library_search_bound(self):
        game = self.session(91271104)
        source = self.add(game.engine, "Burning-Rune Demon", zone="hand")
        hidden = self.add(game.engine, "Grizzly Bears", zone="library", ref="pair-below-live-search-bound")
        hidden.known_to = []
        hidden.revealed_to = []
        for index in range(4):
            self.add(game.engine, "Plains", zone="library", ref=f"pair-bounded-plains-{index}")
        self.add(game.engine, "Aven Mindcensor", seat="B")
        before = self.announce(game, source)
        schema = self.start(game)
        self.assertEqual([0], schema["allowed_counts"])
        self.assertFalse(schema["legal_refs"])
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)["decision"]))
        self.submit(game, "A", accept=False, search_cards=[hidden.ref])
        self.submit(game, "A", search_cards=[])
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual("library", game.state.cards[hidden.object_id].zone)
        self.replay(game, load=True)

    def test_demon_public_pair_replacement_resumes_exactly_before_shuffle(self):
        game, first, second, _, _, _, before = self.scene(91271108, replace_graveyard=True)
        self.start(game)
        self.submit(game, "A", search_cards=[first.ref, second.ref])
        self.submit(game, "A", opponent="C")
        self.submit(game, "C", pile="second")
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(before, self.shuffles(game))
        self.assertEqual("library", game.state.cards[first.object_id].zone)
        self.assertEqual("library", game.state.cards[second.object_id].zone)
        game = self.replay(game, load=True)
        option = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
        self.submit(game, "A", choices={"replacement": option})
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual("exile", game.state.cards[first.object_id].zone)
        self.assertEqual("hand", game.state.cards[second.object_id].zone)
        self.replay(game, load=True)
