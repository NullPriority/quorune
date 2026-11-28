"""Protean Hulk resolves a private combined-mana-value search through normal player actions."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


HULK = "Protean Hulk"
PROFILE = "commander_review"
DEATH_CAPABILITY = "trigger.event.normalized_zone_change"
SEARCH_CAPABILITY = "library.search.fixed_counted"


class CreatureSubsetManaValueSearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "creature-subset-search.sqlite3"
        build_fixture_database(
            ROOT / "tests/fixtures/creature-subset-mana-value-search-cards.json", path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Creature subset search",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def scene(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        hulk = self.add(game.engine, HULK, zone="hand", ref="subset-search-source")
        game.engine.move_card(hulk.object_id, "battlefield", controller="B", log=False)
        outlet = self.add(game.engine, "Goblin Bombardment", seat="B")
        self.assert_closed(game, hulk)
        self.assert_closed(game, outlet)
        cards = {
            "bear": self.add(game.engine, "Grizzly Bears", seat="B", zone="library", ref="subset-private-bear"),
            "giant": self.add(game.engine, "Hill Giant", seat="B", zone="library", ref="subset-private-giant"),
            "six": self.add(game.engine, "Craw Wurm", seat="B", zone="library", ref="subset-private-six"),
            "eight": self.add(game.engine, "Scaled Wurm", seat="B", zone="library", ref="subset-private-eight"),
            "zero": self.add(game.engine, "Memnite", seat="B", zone="library", ref="subset-private-zero"),
            "x": self.add(game.engine, "Stonecoil Serpent", seat="B", zone="library", ref="subset-private-x"),
            "artifact": self.add(game.engine, "Sol Ring", seat="B", zone="library", ref="subset-private-artifact"),
            "back_creature": self.add(
                game.engine, "Westvale Abbey // Ormendahl, Profane Prince",
                seat="B", zone="library", ref="subset-private-back-creature",
            ),
            "other_library": self.add(game.engine, "Centaur Courser", zone="library", ref="subset-owner-library"),
        }
        return game, hulk, outlet, cards

    def start_search(self, game, hulk, outlet):
        self.prepare(game, active="B", actor="B")
        self.checkpoint(game)
        current_hulk = game.state.cards[hulk.object_id]
        self.assertEqual(("A", "B"), (current_hulk.owner, current_hulk.controller))
        legal = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        ability = next(row for row in legal if row["id"].startswith(f"activate:{outlet.ref}:"))
        result = game.act("pilot:B", {
            "action_id": ability["id"], "cost_cards": [hulk.ref], "targets": ["D"], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)
        self.assertIn(hulk.object_id, game.state.players["A"].zones["graveyard"])
        trigger, = (item for item in game.state.stack if item.source_object_id == hulk.object_id)
        self.assertEqual(("triggered_ability", "B"), (trigger.kind, trigger.controller))
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["B"]), (pending.kind, list(pending.actors)))
        return game.packet("pilot:B", full=True)["decision"]

    def search(self, game, *cards):
        result = game.act("pilot:B", {"action_id": "choose", "search_cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)

    def shuffles(self, game):
        return game.state.players["B"].stats.get("shuffle_count", 0)

    def test_death_trigger_offers_private_controller_subset_and_exact_replay(self):
        game, hulk, outlet, cards = self.scene(49923100)
        owner_library = tuple(game.state.players["A"].zones["library"])
        shuffles = self.shuffles(game)
        decision = self.start_search(game, hulk, outlet)

        choice, = (row for row in decision["legal_actions"] if row["id"] == "choose")
        schema = choice["choice_schema"]
        expected = {cards[key].ref: value for key, value in (
            ("bear", 2), ("giant", 4), ("six", 6), ("zero", 0), ("x", 0),
        )}
        offered = {row["id"]: row["mana_value"] for row in decision["ctx"]["search_cards"]}
        self.assertEqual(expected, offered)
        self.assertEqual(set(expected), set(schema["legal_refs"]))
        self.assertEqual((0, 5, 6), (
            schema["minimum"], schema["maximum"], schema["combined_mana_value_maximum"],
        ))
        self.assertTrue(schema["rules_may_fail_to_find"])
        for seat in "ACD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for key, card in cards.items():
                if key != "other_library":
                    self.assertNotIn(card.ref, packet)
                    self.assertNotIn(card.printed_name, packet)

        game = self.replay(game, load=True)
        before = tuple(game.state.players["B"].zones["library"])
        selected = [cards[key] for key in ("bear", "giant", "zero")]
        self.search(game, *selected)
        self.assertIsNone(self.resolve(game))
        for card in selected:
            actual = game.state.cards[card.object_id]
            self.assertEqual(("battlefield", "B"), (actual.zone, actual.controller))
        self.assertEqual(owner_library, tuple(game.state.players["A"].zones["library"]))
        self.assertEqual(shuffles + 1, self.shuffles(game))
        removed = {card.object_id for card in selected}
        remaining = tuple(object_id for object_id in before if object_id not in removed)
        actual_order = tuple(game.state.players["B"].zones["library"])
        self.assertCountEqual(remaining, actual_order)
        self.assertNotEqual(remaining, actual_order)
        for seat in "ACD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card in selected:
                self.assertIn(card.ref, packet)
            for key in ("six", "eight", "x", "artifact", "back_creature"):
                self.assertNotIn(cards[key].ref, packet)
                self.assertNotIn(cards[key].printed_name, packet)
        self.replay(game)

    def test_rejects_over_budget_duplicate_forged_and_wrong_seat_without_mutation(self):
        game, hulk, outlet, cards = self.scene(49923101)
        self.start_search(game, hulk, outlet)
        before = authoritative_state_hash(game.state)
        bear, six = cards["bear"].ref, cards["six"].ref
        attempts = (
            ("B", [bear, six]), ("B", [bear, bear]), ("B", ["forged-subset-ref"]),
            ("B", [cards["other_library"].ref]), ("B", [cards["eight"].ref]),
            ("B", [cards["artifact"].ref]), ("B", [cards["back_creature"].ref]),
            ("B", [bear, 7]), ("A", [bear]), ("C", [bear]),
        )
        for seat, refs in attempts:
            with self.subTest(seat=seat, refs=refs):
                result = game.act(f"pilot:{seat}", {"action_id": "choose", "search_cards": refs})
                self.assertFalse(result.ok, result.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
        self.search(game, cards["six"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[cards["six"].object_id].zone)

    def test_zero_selection_shuffles_and_x_counts_as_zero_in_library(self):
        for select in (False, True):
            with self.subTest(select=select):
                game, hulk, outlet, cards = self.scene(49923102 + select)
                shuffles = self.shuffles(game)
                decision = self.start_search(game, hulk, outlet)
                option = next(row for row in decision["ctx"]["search_cards"] if row["id"] == cards["x"].ref)
                self.assertEqual(0, option["mana_value"])
                initial_x = game.state.cards[cards["x"].object_id].logical_object_id
                selected = (cards["six"], cards["x"]) if select else ()
                self.search(game, *selected)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(shuffles + 1, self.shuffles(game))
                six = game.state.cards[cards["six"].object_id]
                x = game.state.cards[cards["x"].object_id]
                if select:
                    self.assertEqual("battlefield", six.zone)
                    self.assertEqual("graveyard", x.zone)
                    self.assertNotEqual(initial_x, x.logical_object_id)
                else:
                    self.assertEqual(("library", "library"), (six.zone, x.zone))
                self.replay(game)

    def test_selected_creatures_enter_simultaneously_for_replacement(self):
        game, hulk, outlet, cards = self.scene(49923104)
        grumgully = self.add(game.engine, "Grumgully, the Generous", seat="B", zone="library")
        centaur = self.add(game.engine, "Centaur Courser", seat="B", zone="library")
        self.assert_closed(game, grumgully)
        self.start_search(game, hulk, outlet)
        self.search(game, grumgully, centaur)
        self.assertIsNone(self.resolve(game))
        for card in (grumgully, centaur):
            current = game.state.cards[card.object_id]
            self.assertEqual("battlefield", current.zone)
            self.assertNotIn("+1/+1", current.counters)

        game, hulk, outlet, cards = self.scene(49923105)
        grumgully = self.add(game.engine, "Grumgully, the Generous", seat="B")
        scales = self.add(game.engine, "Hardened Scales", seat="B")
        season = self.add(game.engine, "Doubling Season", seat="B")
        for permanent in (grumgully, scales, season):
            self.assert_closed(game, permanent)
        shuffles = self.shuffles(game)
        self.start_search(game, hulk, outlet)
        selected = (cards["bear"], cards["giant"])
        self.search(game, *selected)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(shuffles, self.shuffles(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in selected))
        for seat in "ACD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card in selected:
                self.assertNotIn(card.ref, packet)

        game = self.replay(game, load=True)
        for _ in range(4):
            pending = game.state.pending_decision
            if pending is None or pending.kind != "replacement.order":
                break
            options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
            option = next(row for row in options if "hardened" in (row["id"] + row["label"]).casefold())
            result = game.act("pilot:B", {"action_id": "choose", "replacement": option["id"]})
            self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(shuffles + 1, self.shuffles(game))
        for card in selected:
            current = game.state.cards[card.object_id]
            self.assertEqual(("battlefield", 4), (current.zone, current.counters.get("+1/+1")))
        self.replay(game)

    def test_whole_card_requires_the_exact_search_and_independent_capability(self):
        original = self.db.lookup("Protean Hulk", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Protean Hulk")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        keys = {CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY, DEATH_CAPABILITY, SEARCH_CAPABILITY}
        program, = (item for item in semantics.programs_for_oracle(original.oracle_id)
                    if CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY in item.capability_dependencies)
        self.assertLessEqual(keys, set(program.capability_dependencies))

        for withheld in sorted(keys):
            with self.subTest(withheld=withheld):
                deps = tuple(key for key in program.capability_dependencies if key != withheld)
                changed = replace(
                    program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict(),
                )
                runtime = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(runtime["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{withheld}", runtime["blockers"])

        def compile_card(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)

        self.assertEqual("exact", compile_card(original).status)
        for before, after in (
            ("your library", "target player's library"),
            ("creature cards", "creature or land cards"),
            ("or less", "or greater"),
            ("then shuffle.", "then shuffle. Rewrite history."),
        ):
            with self.subTest(after=after):
                changed = replace(original, oracle_text=original.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_card(changed).status)

        with patch("quorune.compiler.library_search_templates.creature_subset_mana_value_search_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_card(original).status)
            vanilla = replace(original, oracle_text="Draw a card.", type_line="Sorcery", power=None, toughness=None)
            self.assertEqual("exact", compile_card(vanilla).status)


if __name__ == "__main__":
    unittest.main()
