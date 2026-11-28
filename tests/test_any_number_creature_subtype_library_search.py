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
            fixtures / "changeling-characteristics-cards.json",
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


    def begin_world_tree(self, seed, *, replacement=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "The World Tree", ref="world-tree-source")
        names = ("Trial Library God", "Trial Library God Two", "Trial God Relic",
                 "Universal Automaton", "Trial God Kindred Instant", ELVES[0])
        cards = {name: self.add(game.engine, name, zone="library", ref=f"world-tree-private-{index}")
                 for index, name in enumerate(names)}
        foreign = self.add(game.engine, names[0], seat="B", zone="library", ref="world-tree-foreign")
        extras = [self.add(game.engine, name) for name in
                  ("Grumgully, the Generous", "Hardened Scales", "Doubling Season")] if replacement else []
        for card in (source, *cards.values(), *extras):
            self.assert_closed(game, card)
        self.prepare(game, mana=dict.fromkeys("WUBRG", 2))
        self.checkpoint(game)
        before = self.shuffles(game)
        offer, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                  if row["id"].startswith(f"activate:{source.ref}:") and
                  sum(row.get("cost_summary", {}).get("m", {}).values()) == 10)
        rejected = game.act("pilot:B", {"action_id": offer["id"]})
        self.assertFalse(rejected.ok)
        activated = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, list(pending.actors)))
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, len(names) - 1), (schema["minimum"], schema["maximum"]))
        self.assertEqual({card.ref for name, card in cards.items() if name != ELVES[0]}, set(schema["legal_refs"]))
        for seat in "BCD":
            projected = json.dumps(game.packet(f"pilot:{seat}", full=True))
            self.assertTrue(all(card.ref not in projected for card in cards.values()))
        return game, cards, foreign, before

    def test_world_tree_selects_god_subtype_including_changeling_and_kindred_then_shuffles(self):
        for find in (True, False):
            with self.subTest(find=find):
                game, cards, foreign, before = self.begin_world_tree(176114028 + int(find))
                game = self.replay(game, load=True)
                snapshot = authoritative_state_hash(game.state)
                for actor, chosen in (
                    ("B", cards["Trial Library God"]), ("A", cards[ELVES[0]]), ("A", foreign),
                ):
                    self.assertFalse(self.choose(game, actor, search_cards=[chosen.ref]).ok)
                    self.assertEqual(snapshot, authoritative_state_hash(game.state))
                chosen_names = ("Trial Library God", "Trial God Relic", "Universal Automaton",
                                "Trial God Kindred Instant")
                result = self.choose(game, "A", search_cards=[cards[name].ref for name in chosen_names] if find else [])
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(before + 1, self.shuffles(game))
                for name, card in cards.items():
                    wanted = "battlefield" if find and name in chosen_names[:3] else "library"
                    self.assertEqual(wanted, game.state.cards[card.object_id].zone, name)
                opposing = json.dumps(game.packet("pilot:B", full=True))
                for name in ("Trial God Kindred Instant", "Trial Library God Two", ELVES[0]):
                    self.assertNotIn(cards[name].ref, opposing)
                self.replay(game, load=True)

    def test_world_tree_replays_private_competing_replacements_for_one_simultaneous_entry(self):
        game, cards, _, before = self.begin_world_tree(176114030, replacement=True)
        gods = [cards[name] for name in ("Trial Library God", "Trial Library God Two")]
        instant = cards["Trial God Kindred Instant"]
        result = self.choose(game, "A", search_cards=[card.ref for card in (*gods, instant)])
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(before, self.shuffles(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in (*gods, instant)))
        self.assertNotIn(instant.ref, json.dumps(game.packet("pilot:B", full=True)))
        game = self.replay(game, load=True)
        for _ in range(4):
            pending = game.state.pending_decision
            if pending is None or pending.kind != "replacement.order":
                break
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            option = next(row["id"] for row in options if "hardened" in (row["id"] + row["label"]).casefold())
            result = self.choose(game, "A", replacement=option)
            self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, self.shuffles(game))
        self.assertEqual([4, 4], [game.state.cards[card.object_id].counters.get("+1/+1") for card in gods])
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in gods))
        self.assertEqual("library", game.state.cards[instant.object_id].zone)
        self.assertNotIn(instant.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_world_tree_subtype_wire_is_exact_and_legacy_creature_scope_is_retained(self):
        from unittest.mock import patch
        from quorune.compiler.library_search_templates import fixed_library_search_effect_template
        from quorune.library_search_model import (
            ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION, ANY_NUMBER_SUBTYPE_BATTLEFIELD_CAPABILITY,
            ANY_NUMBER_SUBTYPE_BATTLEFIELD_VERSION, AnyNumberCreatureSubtypeSearch,
            counted_search_bound_dependencies,
        )
        from quorune.oracle_ir import compile_oracle_card

        card = self.db.lookup("The World Tree", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (node for face in ir.faces for node in face.nodes if any(
            effect.get("schema_version") == ANY_NUMBER_SUBTYPE_BATTLEFIELD_VERSION for effect in node.effects
        ))
        effect = AnyNumberCreatureSubtypeSearch.from_effect(node.effects[0]).effect()
        self.assertEqual({"subtypes_any": ["god"]}, effect["selector"])
        self.assertIn(ANY_NUMBER_SUBTYPE_BATTLEFIELD_CAPABILITY, counted_search_bound_dependencies(effect))
        for changed in (
            {**effect, "reveal": True}, {**effect, "shuffle_after": False},
            {**effect, "selector": {"types_any": ["creature"], "subtypes_any": ["god"]}},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                counted_search_bound_dependencies(changed)
        older = fixed_library_search_effect_template(
            "Search your library for any number of Elf creature cards, put them onto the battlefield, then shuffle.",
        ).effect()
        self.assertEqual(ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION, older["schema_version"])
        self.assertEqual({"types_any": ["creature"], "subtypes_any": ["elf"]}, older["selector"])
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime

        game = self.session(176114032)
        self.add(game.engine, card.name)
        program, = (program for program in game.engine.semantics.programs_for_oracle(card.oracle_id) if any(
            result.get("schema_version") == ANY_NUMBER_SUBTYPE_BATTLEFIELD_VERSION for result in program.effects
        ))
        stripped = tuple(value for value in program.capability_dependencies
                         if value != ANY_NUMBER_SUBTYPE_BATTLEFIELD_CAPABILITY)
        changed = replace(program, capability_dependencies=stripped,
                          capability_closure=self.registry.closure(stripped, profile="commander_review").to_dict())
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.library_search_templates.canonical_creature_subtype", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)

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
