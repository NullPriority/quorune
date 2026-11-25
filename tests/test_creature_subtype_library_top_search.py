"""Search by creature subtype, reveal the results, and order them privately on top."""

from dataclasses import replace
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_partition import commit_ordered_library_partition
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


GOBLIN_RECRUITER = "Goblin Recruiter"
DWARVEN_RECRUITER = "Dwarven Recruiter"


class CreatureSubtypeLibraryTopSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "subtype-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/creature-subtype-library-top-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Ordered subtype search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_original_entry_triggers_register_a_mandatory_subtype_search_and_private_top_order(self):
        for name, subtype in ((GOBLIN_RECRUITER, "goblin"), (DWARVEN_RECRUITER, "dwarf")):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                self.assertEqual(("permanent.enter.self", "battlefield"), (program.event, program.active_zone))
                self.assertIsNone(program.target_schema)
                search, = program.effects
                self.assertEqual(("search", 12, [subtype], "library_top"),
                                 (search["op"], search["schema_version"], search["selector"]["subtypes_any"],
                                  search["destination"]))
                self.assertTrue(search["reveal"])
                self.assertTrue(search["shuffle_before_placement"])
                for text in (
                    card.oracle_text.replace("this creature enters", "this creature dies"),
                    card.oracle_text.replace("search your library", "you may search your library"),
                    card.oracle_text.replace("reveal them, then shuffle", "shuffle, then reveal them"),
                    card.oracle_text.replace("put those cards on top", "put those cards on the bottom"),
                    card.oracle_text + " Rewrite the game history.",
                ):
                    self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                        capability_registry=self.registry).status, text)

    def test_raw_codec_rejects_widening_the_audience_selector_and_result_order(self):
        card = self.db.lookup(GOBLIN_RECRUITER, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, card.name)
        program, = semantics.programs_for_oracle(card.oracle_id)
        search, = program.effects
        for changed in (
            replace(program, event="creature.dies.self"),
            replace(program, effects=({**search, "searching_player": "B"},)),
            replace(program, effects=({**search, "selector": {"names": ["Goblin War Paint"]}},)),
            replace(program, effects=({**search, "shuffle_before_placement": False},)),
            replace(program, effects=({**search, "shuffle_after": True},)),
            replace(program, effects=({**search, "reveal": False},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def scene(self, name, seed, *, restriction=None, deep=False, matches=True):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, name, zone="hand")
        if name == GOBLIN_RECRUITER:
            pool, wrong_name = ("Tarfire", "Goblin Piker", "Goblin Recruiter"), "Goblin War Paint"
        else:
            pool, wrong_name = ("Dwarven Grunt", "Dwarven Trader", "Dwarven Priest"), "Dwarven Mine"
        matching = [self.add(engine, candidate, zone="library", ref=f"recruiter-result-{index}")
                    for index, candidate in enumerate(pool) if matches]
        wrong = self.add(engine, wrong_name, zone="library", ref="recruiter-name-only")
        other = self.add(engine, pool[0], seat="B", zone="library", ref="recruiter-other-library")
        fillers = []
        if deep:
            fillers = [self.add(engine, "Plains", zone="library", ref=f"recruiter-top-filler-{index}")
                       for index in range(4)]
        for card in [*matching, wrong, other, *fillers]:
            card.known_to.clear()
            card.revealed_to.clear()
        if restriction:
            control = self.add(engine, restriction, seat="B")
            self.assert_closed(game, control)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        return game, matching, wrong, other

    def offer(self, game, matches, excluded):
        decision = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, decision.actors))
        schema = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
        self.assertEqual("top_to_bottom", schema["order"])
        self.assertEqual((0, len(matches)), (schema["minimum"], schema["maximum"]))
        self.assertEqual({card.ref for card in matches}, set(schema["legal_refs"]))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            for card in [*matches, *excluded]:
                self.assertNotIn(card.ref, json.dumps(packet))

    def choose(self, game, cards, *, seat="A", ok=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": [card.ref for card in cards]})
        self.assertEqual(ok, result.ok, result.summary)
        if not ok:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def shuffle_count(self, game):
        return game.state.players["A"].stats.get("shuffle_count", 0)

    def assert_ordered_result(self, game, chosen, original, next_shuffle):
        ids = [card.object_id for card in chosen]
        shuffled = [object_id for object_id in original if object_id not in ids]
        Random(f"{game.state.config.seed}|A|shuffle|{next_shuffle}").shuffle(shuffled)
        self.assertEqual(shuffled + ids[::-1], game.state.players["A"].zones["library"],
                         "Chosen cards retain the announced private top-to-bottom order")
        self.assertEqual(next_shuffle, self.shuffle_count(game))
        for card in chosen:
            self.assertEqual("library", game.state.cards[card.object_id].zone)
        reveal = next(e for e in reversed(game.state.events) if e.code == "library.search.reveal")
        shuffle = next(e for e in reversed(game.state.events) if e.code == "library.shuffle")
        result = next(e for e in reversed(game.state.events) if e.code == "library.search")
        self.assertLess(reveal.event_id, shuffle.event_id)
        self.assertLess(shuffle.event_id, result.event_id)
        self.assertEqual(sorted(card.ref for card in chosen), reveal.details["objects"])
        self.assertEqual(reveal.details["objects"], result.details["objects"])
        self.assertEqual([card.object_id for card in sorted(chosen, key=lambda value: value.ref)],
                         result.changed_objects)
        projection = game.packet("pilot:A", full=True)["state"]["players"]["A"]
        self.assertEqual([card.ref for card in chosen], [row["id"] for row in projection["known_top"]])

    def test_real_goblin_recruiter_finds_kindred_and_hides_the_chosen_top_order(self):
        game, cards, wrong, other = self.scene(GOBLIN_RECRUITER, 61342101)
        self.offer(game, cards, (wrong, other))
        self.choose(game, [cards[0]], seat="B", ok=False)
        self.choose(game, [wrong], ok=False)
        self.choose(game, [other], ok=False)
        self.choose(game, [cards[0], cards[0]], ok=False)
        game = self.replay(game, load=True)
        alternate = self.replay(game, load=True)
        original = list(game.state.players["A"].zones["library"])
        next_shuffle = self.shuffle_count(game) + 1
        self.choose(game, cards)
        self.choose(alternate, cards[::-1])
        self.assert_ordered_result(game, cards, original, next_shuffle)
        self.assert_ordered_result(alternate, cards[::-1], original, next_shuffle)
        public_names = [card.printed_name for card in sorted(cards, key=lambda value: value.ref)]
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(viewer, full=True, cursor_key="first-result-" + viewer)
            opposite = alternate.packet(viewer, full=True, cursor_key="second-result-" + viewer)
            self.assertNotIn("known_top", packet["state"]["players"]["A"])
            self.assertEqual(packet["state"], opposite["state"])
            self.assertEqual(packet.get("events"), opposite.get("events"))
            published = next(event for event in packet["events"] if event["c"] == "library.search.reveal")
            self.assertEqual(public_names, published["d"]["names"])
            self.assertTrue(all(name in published["s"] for name in public_names))
            self.assertNotIn(wrong.ref, json.dumps(packet))
        for trial in (game, alternate):
            self.replay(trial, load=True)

    def test_real_dwarven_recruiter_orders_only_the_found_subset(self):
        game, cards, wrong, other = self.scene(DWARVEN_RECRUITER, 61342102)
        self.offer(game, cards, (wrong, other))
        original = list(game.state.players["A"].zones["library"])
        next_shuffle = self.shuffle_count(game) + 1
        chosen = [cards[2], cards[0]]
        self.choose(game, chosen)
        self.assert_ordered_result(game, chosen, original, next_shuffle)
        self.assertEqual([], game.state.cards[cards[1].object_id].known_to)
        self.replay(game, load=True)

    def test_reversing_the_selected_top_order_fails_the_real_recruiter_result(self):
        def reversed_order(library, *, top_top_first):
            commit_ordered_library_partition(library, top_top_first=top_top_first[::-1])

        with patch("quorune.rules.library_partition.commit_ordered_library_partition",
                   side_effect=reversed_order):
            with self.assertRaisesRegex(AssertionError, "Chosen cards retain the announced private top-to-bottom order"):
                self.test_real_dwarven_recruiter_orders_only_the_found_subset()

    def test_zero_matches_and_actual_search_restrictions_still_shuffle_once(self):
        scenes = (
            {"matches": False}, {"restriction": "Aven Mindcensor", "deep": True},
            {"restriction": "Mindlock Orb"},
        )
        for index, parameters in enumerate(scenes):
            with self.subTest(parameters=parameters):
                game, cards, wrong, other = self.scene(DWARVEN_RECRUITER, 61342110 + index, **parameters)
                before = self.shuffle_count(game)
                if parameters.get("restriction") == "Mindlock Orb":
                    self.assertIsNone(self.resolve(game))
                else:
                    self.offer(game, [], (*cards, wrong, other))
                    if cards:
                        self.choose(game, [cards[0]], ok=False)
                    self.choose(game, [])
                self.assertEqual(before + 1, self.shuffle_count(game))
                self.assertFalse(any(event.code == "library.search.reveal" for event in game.state.events))
                self.replay(game, load=True)
