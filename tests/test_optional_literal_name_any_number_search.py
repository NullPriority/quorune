"""Native resolution of optional searches for any number of explicitly named cards."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Battalion Foot Soldier", "Gathering Throng", "Legion Conquistador")


class OptionalLiteralNameAnyNumberSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-named-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/optional-literal-name-any-number-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional named search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_all_three_original_entry_triggers_register_and_preserve_the_printed_literal(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                self.assertEqual(("permanent.enter.self", "battlefield"), (program.event, program.active_zone))
                self.assertIsNone(program.target_schema)
                wrapper, = program.effects
                self.assertEqual(("offer_optional_effect", "$controller"), (wrapper["op"], wrapper["player"]))
                search, = wrapper["effects"]
                self.assertEqual(11, search["schema_version"])
                self.assertEqual({"names": [name]}, search["selector"])
                for text in (
                    card.oracle_text.replace("this creature enters", "this creature dies"),
                    card.oracle_text.replace("you may search", "search"),
                    card.oracle_text.replace("your library", "an opponent's library"),
                    card.oracle_text.replace("put them into your hand", "put them onto the battlefield"),
                    card.oracle_text + " Rewrite the game history.",
                ):
                    self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                        capability_registry=self.registry).status, text)

    def test_closed_result_rejects_a_client_bound_unwrapped_search_and_alternate_actor(self):
        card = self.db.lookup(ORIGINALS[0], fuzzy=False)
        _, semantics = register_card(self.db, self.registry, card.name)
        program, = semantics.programs_for_oracle(card.oracle_id)
        wrapper, = program.effects
        search, = wrapper["effects"]
        for changed in (
            replace(program, effects=(search,)),
            replace(program, event="creature.dies.self"),
            replace(program, effects=({**wrapper, "player": "B"},)),
            replace(program, effects=({**wrapper, "effects": [{**search, "count": {"minimum": 0, "maximum": 10}}]},)),
            replace(program, effects=({**wrapper, "effects": [{**search, "reveal": False}]},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def scene(self, name, seed, *, quantity=3, restriction=None, deep=False):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, name, zone="hand")
        matches = [self.add(engine, name, zone="library", ref=f"named-search-match-{index}")
                   for index in range(quantity)]
        wrong = self.add(engine, "Grizzly Bears", zone="library", ref="named-search-wrong")
        top_fillers = []
        if deep:
            top_fillers = [self.add(engine, "Plains", zone="library", ref=f"named-search-top-{index}")
                           for index in range(4)]
        other = self.add(engine, name, seat="B", zone="library", ref="named-search-opponent")
        for card in [*matches, wrong, other, *top_fillers]:
            card.known_to.clear()
            card.revealed_to.clear()
        if restriction:
            control = self.add(engine, restriction, seat="B")
            self.assert_closed(game, control)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        return game, matches, wrong, other

    def immediate_choice(self, game, cards):
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            self.assertTrue(all(card.ref not in packet for card in cards))

    def choose(self, game, **fields):
        result = game.act("pilot:A", {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, principal, **fields):
        before = authoritative_state_hash(game.state)
        result = game.act(principal, {"action_id": "choose", **fields})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def search_schema(self, game):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, decision.actors))
        packet = game.packet("pilot:A", full=True)
        return packet["decision"]["legal_actions"][0]["choice_schema"]

    @staticmethod
    def shuffle_count(game):
        return game.state.players["A"].stats.get("shuffle_count", 0)

    def test_paid_original_can_find_more_than_ten_and_reveals_only_selected_cards(self):
        game, matches, wrong, other = self.scene("Gathering Throng", 61342001, quantity=12)
        protected = [*matches, wrong, other]
        self.immediate_choice(game, protected)
        self.reject(game, "pilot:B", choice="apply")
        self.choose(game, choice="apply")
        schema = self.search_schema(game)
        self.assertEqual((0, 12), (schema["minimum"], schema["maximum"]),
                         "Every eligible card must fit the printed any-number limit")
        self.assertEqual({card.ref for card in matches}, set(schema["legal_refs"]))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(principal, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(card.ref not in json.dumps(packet) for card in protected))
        self.reject(game, "pilot:B", search_cards=[matches[0].ref])
        self.reject(game, "pilot:A", search_cards=[wrong.ref])
        self.reject(game, "pilot:A", search_cards=[other.ref])
        self.reject(game, "pilot:A", search_cards=[matches[0].ref, matches[0].ref])
        game = self.replay(game, load=True)
        shuffles = self.shuffle_count(game)
        self.choose(game, search_cards=[card.ref for card in matches[:11]])
        self.assertEqual(shuffles + 1, self.shuffle_count(game))
        for card in matches[:11]:
            moved = game.state.cards[card.object_id]
            self.assertEqual("hand", moved.zone)
            self.assertTrue(set(game.engine.seats) <= set(moved.known_to))
        for card in (matches[-1], wrong, other):
            remaining = game.state.cards[card.object_id]
            self.assertEqual("library", remaining.zone)
            self.assertEqual([], remaining.known_to)
        self.replay(game, load=True)

    def test_a_fixed_count_cap_mutation_fails_the_real_private_offer(self):
        with patch("quorune.selection.searching._semantic_search_maximum", return_value=10):
            with self.assertRaisesRegex(AssertionError, "Every eligible card must fit the printed any-number limit"):
                self.test_paid_original_can_find_more_than_ten_and_reveals_only_selected_cards()

    def test_optional_decline_and_accepted_zero_have_distinct_shuffle_and_search_results(self):
        for index, option in enumerate(("decline", "apply")):
            with self.subTest(option=option):
                game, matches, wrong, other = self.scene(ORIGINALS[index * 2], 61342002 + index)
                self.immediate_choice(game, [*matches, wrong, other])
                before = self.shuffle_count(game)
                game = self.replay(game, load=True)
                self.choose(game, choice=option)
                if option == "apply":
                    self.assertEqual(3, self.search_schema(game)["maximum"])
                    self.choose(game, search_cards=[])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(before + int(option == "apply"), self.shuffle_count(game))
                self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in matches))
                self.replay(game, load=True)

    def test_real_restrictions_prohibit_the_optional_search_or_limit_its_private_eligible_count(self):
        game, matches, _, _ = self.scene(ORIGINALS[0], 61342004, restriction="Mindlock Orb")
        before = self.shuffle_count(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, self.shuffle_count(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in matches))
        self.replay(game, load=True)

        game, matches, wrong, other = self.scene(
            ORIGINALS[2], 61342005, restriction="Aven Mindcensor", deep=True,
        )
        before = self.shuffle_count(game)
        self.immediate_choice(game, [*matches, wrong, other])
        self.choose(game, choice="apply")
        schema = self.search_schema(game)
        self.assertEqual((0, 0, []), (schema["minimum"], schema["maximum"], schema["legal_refs"]))
        self.reject(game, "pilot:A", search_cards=[matches[0].ref])
        self.choose(game, search_cards=[])
        self.assertEqual(before + 1, self.shuffle_count(game))
        self.replay(game, load=True)
