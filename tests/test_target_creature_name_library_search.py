"""Search the controller's actual library using the current legal creature name."""

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


BIFURCATE = "Bifurcate"
PACK_HUNT = "Pack Hunt"


class TargetCreatureNameLibrarySearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-creature-name-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/target-creature-name-library-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current creature name search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_distinct_targets_quantities_and_destinations(self):
        for name in (BIFURCATE, PACK_HUNT):
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
                instruction, = program.effects
                self.assertEqual(("search", 10, "$target.0"), (
                    instruction["op"], instruction["schema_version"], instruction["name_target"],
                ))
                self.assertEqual(["creature"], program.target_schema["types_any"])
                self.assertEqual(name == BIFURCATE, program.target_schema.get("token") is False)
                for text in (
                    card.oracle_text.replace("same name as target", "same name as another target"),
                    card.oracle_text.replace("your library", "an opponent's library"),
                    card.oracle_text + " Rewrite the game history.",
                ):
                    self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                        capability_registry=self.registry).status, text)

    def test_raw_scope_rejects_alternate_anchor_and_broad_target_authority(self):
        card = self.db.lookup(BIFURCATE, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, BIFURCATE)
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        effect, = program.effects
        target = {key: value for key, value in program.target_schema.items() if key != "token"}
        for changed in (
            replace(program, target_schema=target),
            replace(program, event="activate"),
            replace(program, effects=({**effect, "name_target": "$source.zone_object"},)),
            replace(program, effects=({**effect, "name_target": "$target.1"},)),
            replace(program, effects=({**effect, "selector": {"names": ["Rat Colony"]}},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def scene(self, name, seed, *, response=False):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, name, zone="hand")
        target = self.add(engine, "Rat Colony", seat="B", ref="name-search-target")
        rats = [self.add(engine, "Rat Colony", zone="library", ref=f"name-search-rat-{index}")
                for index in range(4)]
        wrong = self.add(engine, "Grizzly Bears", zone="library", ref="name-search-other-card")
        bounce = None
        if response:
            bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
            self.add(engine, "Island", seat="B")
            self.assert_closed(game, bounce)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [target.ref])
        return game, source, target, rats, wrong, bounce

    def pending_search(self, game, rats, wrong, maximum):
        decision = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, decision.actors))
        offer = game.packet("pilot:A", full=True)["decision"]
        schema = offer["legal_actions"][0]["choice_schema"]
        self.assertEqual(0, schema["minimum"])
        self.assertEqual(maximum, schema["maximum"])
        self.assertEqual({rat.ref for rat in rats}, set(schema["legal_refs"]),
                         "Only the current creature's names qualify in the private search")
        self.assertNotIn(wrong.ref, schema["legal_refs"])
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(wrong.ref, json.dumps(packet))
            self.assertTrue(all(rat.ref not in json.dumps(packet) for rat in rats))

    def shuffle_count(self, game):
        return sum(event.code == "library.shuffle" for event in game.state.events)

    def test_actual_bifurcate_places_the_selected_matching_permanent_and_preserves_private_alternatives(self):
        game, _, _, rats, wrong, _ = self.scene(BIFURCATE, 61341901)
        self.pending_search(game, rats, wrong, 1)
        digest = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "search_cards": [rats[0].ref]}
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "search_cards": [wrong.ref]}).ok)
        self.assertEqual(digest, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        shuffles = self.shuffle_count(game)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        chosen = game.state.cards[rats[0].object_id]
        self.assertEqual(("battlefield", "A"), (chosen.zone, chosen.controller))
        self.assertTrue(all(game.state.cards[rat.object_id].zone == "library" for rat in rats[1:]))
        self.assertEqual(shuffles + 1, self.shuffle_count(game))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(rats[1].ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_pack_hunt_reveals_only_selected_cards_and_enforces_the_printed_three_card_limit(self):
        game, _, _, rats, wrong, _ = self.scene(PACK_HUNT, 61341902)
        self.pending_search(game, rats, wrong, 3)
        digest = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "search_cards": [r.ref for r in rats]}).ok)
        self.assertEqual(digest, authoritative_state_hash(game.state))
        before = self.shuffle_count(game)
        chosen = rats[:2]
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [r.ref for r in chosen]})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(game.state.cards[r.object_id].zone == "hand" for r in chosen))
        self.assertTrue(all(set(game.engine.seats) <= set(game.state.cards[r.object_id].known_to) for r in chosen))
        self.assertTrue(all("B" not in game.state.cards[r.object_id].known_to for r in rats[2:]))
        self.assertEqual(before + 1, self.shuffle_count(game))
        self.replay(game, load=True)

    def test_legal_decline_shuffles_and_an_actual_illegal_target_never_searches(self):
        game, _, _, rats, wrong, _ = self.scene(BIFURCATE, 61341903)
        self.pending_search(game, rats, wrong, 1)
        before = self.shuffle_count(game)
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before + 1, self.shuffle_count(game))
        self.assertTrue(all(game.state.cards[r.object_id].zone == "library" for r in rats))
        self.replay(game, load=True)

        fresh, _, target, rats, _, bounce = self.scene(PACK_HUNT, 61341904, response=True)
        before = self.shuffle_count(fresh)
        self.cast_for(fresh, "B", bounce, [target.ref])
        self.resolve_top(fresh)
        self.assertEqual("hand", fresh.state.cards[target.object_id].zone)
        self.assertIsNone(self.resolve(fresh))
        self.assertEqual(before, self.shuffle_count(fresh))
        self.assertTrue(all(fresh.state.cards[r.object_id].zone == "library" for r in rats))
        self.replay(fresh, load=True)

    def test_omitting_the_name_comparison_is_killed_by_the_actual_private_search_offer(self):
        with patch("quorune.rules.qualified_library_search.library_card_rules_names",
                   return_value=("Rat Colony",)):
            with self.assertRaisesRegex(AssertionError, "Only the current creature"):
                self.test_actual_bifurcate_places_the_selected_matching_permanent_and_preserves_private_alternatives()

    def test_actual_bifurcate_resumes_the_same_private_selection_after_entry_replacements(self):
        game = self.session(61341905)
        engine = game.engine
        source = self.add(engine, BIFURCATE, zone="hand")
        anchor = self.add(engine, "Spike Feeder", seat="B", ref="name-search-spike-anchor")
        anchor.counters["+1/+1"] = 2
        candidate = self.add(engine, "Spike Feeder", zone="library", ref="name-search-spike-candidate")
        wrong = self.add(engine, "Grizzly Bears", zone="library", ref="name-search-other-entry")
        scales = self.add(engine, "Hardened Scales")
        doubling = self.add(engine, "Doubling Season")
        for card in (source, anchor, candidate, scales, doubling):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [anchor.ref])
        self.pending_search(game, [candidate], wrong, 1)
        shuffles = self.shuffle_count(game)
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": [candidate.ref]})
        self.assertTrue(result.ok, result.summary)
        decision = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        self.assertEqual("library", game.state.cards[candidate.object_id].zone)
        self.assertEqual(shuffles, self.shuffle_count(game))
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        chosen = next(row["id"] for row in rows if "hardened" in (row["id"] + row.get("label", "")).casefold())
        command = {"action_id": "choose", "replacement": chosen}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        retained = game.state.cards[candidate.object_id]
        self.assertEqual(("battlefield", 6), (retained.zone, retained.counters.get("+1/+1")))
        self.assertEqual(shuffles + 1, self.shuffle_count(game))
        self.replay(game, load=True)
