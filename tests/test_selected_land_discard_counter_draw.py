"""Private selected land discards bind independent counter and draw results."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.discard_result_model import SELECTED_LAND_DISCARD_RESULT_VERSION
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Nantuko Cultivator"


class SelectedLandDiscardCounterDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "selected-land-discard-counter-draw.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/selected-land-discard-counter-draw-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private selected land discards", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_one_private_selection_and_one_final_count(self):
        outcome, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(outcome["strict_capability_ready"], outcome["blockers"])
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(("permanent.enter.self", "battlefield"), (program.event, program.active_zone))
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        self.assertEqual(SELECTED_LAND_DISCARD_RESULT_VERSION, effect["schema_version"])
        self.assertEqual(("$controller", ["$controller"], "own", 0),
                         (effect["actor"], effect["players"], effect["reduction"], effect["offset"]))
        self.assertIn("zone.graveyard.ordered_simultaneous_arrival", program.capability_dependencies)
        self.assertIn("counter.producer.fixed_effect", program.capability_dependencies)
        for mutation in (
            {"offset": 1}, {"players": "all"}, {"actor": "B"}, {"selected": []},
        ):
            result = bind_semantic_program_runtime(
                replace(program, effects=[{**effect, **mutation}]),
                capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(result["strict"], result)
        for text in (
            card.oracle_text.replace("land cards", "cards"),
            card.oracle_text.replace("on this creature", "on each creature you control"),
            card.oracle_text.replace("and draw that many cards", "and draw two cards"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry,
            ).status)

    def choose(self, game, cards):
        result = game.act("pilot:A", {"action_id": "choose", "cards": [card.ref for card in cards]})
        self.assertTrue(result.ok, result.summary)

    def offered(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        return game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]

    def reject(self, game, seat, refs):
        before = authoritative_state_hash(game.state)
        rejected = game.act(f"pilot:{seat}", {"action_id": "choose", "cards": list(refs)})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_selected_lands_are_private_and_ordered_then_increase_counters_and_draw_independently(self):
        game = self.session(70190421)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        first = self.add(game.engine, "Forest", zone="hand", ref="cultivator-own-forest")
        second = self.add(game.engine, "Plains", zone="hand", ref="cultivator-own-plains")
        nonland = self.add(game.engine, "Runeclaw Bear", zone="hand", ref="cultivator-own-nonland")
        foreign = self.add(game.engine, "Forest", seat="B", zone="hand", ref="cultivator-foreign-land")
        scales = self.add(game.engine, "Hardened Scales")
        for card in (source, scales):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)

        self.cast(game, "A", source)
        self.resolve_top(game)
        original_library = len(game.state.players["A"].zones["library"])
        selection = self.offered(game)
        self.assertEqual(0, selection["minimum"])
        self.assertLessEqual({first.ref, second.ref}, set(selection["legal_refs"]))
        self.assertNotIn(nonland.ref, selection["legal_refs"])
        self.assertNotIn(foreign.ref, selection["legal_refs"])
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            projected = json.dumps(game.packet(viewer, full=True))
            for card in (first, second, nonland):
                self.assertNotIn(card.ref, projected)
        self.reject(game, "B", [first.ref, second.ref])
        for refs in ([nonland.ref], [foreign.ref], [first.ref, first.ref]):
            self.reject(game, "A", refs)
        self.choose(game, [first, second])
        order = self.offered(game)
        self.assertEqual("bottom_to_top", order["order"])
        self.assertEqual({first.ref, second.ref}, set(order["legal_refs"]))
        self.assertEqual(["hand", "hand"], [game.state.cards[card.object_id].zone for card in (first, second)])
        self.assertEqual(original_library, len(game.state.players["A"].zones["library"]))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        self.reject(game, "A", [first.ref])
        game = self.replay(game, load=True)
        self.choose(game, [second, first])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([second.object_id, first.object_id], game.state.players["A"].zones["graveyard"][-2:])
        self.assertEqual(3, game.state.cards[source.object_id].counters.get("+1/+1"))
        self.assertEqual(original_library - 2, len(game.state.players["A"].zones["library"]))
        self.assertEqual("hand", game.state.cards[nonland.object_id].zone)
        self.replay(game, load=True)

    def test_actual_cloudshift_keeps_discard_and_draw_but_the_original_counters_cannot_follow(self):
        game = self.session(70190422)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        response = self.add(game.engine, "Cloudshift", zone="hand")
        land = self.add(game.engine, "Forest", zone="hand", ref="cultivator-old-trigger-land")
        for card in (source, response):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1, "W": 1})
        self.checkpoint(game)

        self.cast_for(game, "A", source, [], reserve={"W": 1})
        self.resolve_top(game)
        original_identity = game.state.cards[source.object_id].logical_object_id
        self.cast_for(game, "A", response, [source.ref])
        self.resolve_top(game)
        old_library = len(game.state.players["A"].zones["library"])
        self.assertNotEqual(original_identity, game.state.cards[source.object_id].logical_object_id)
        self.offered(game)
        self.choose(game, [])
        self.assertEqual(old_library, len(game.state.players["A"].zones["library"]))
        self.offered(game)
        self.choose(game, [land])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        self.assertEqual(old_library - 1, len(game.state.players["A"].zones["library"]))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_actual_replaced_discard_to_exile_still_contributes_its_committed_count(self):
        game = self.session(70190423)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        land = self.add(game.engine, "Forest", zone="hand", ref="cultivator-exiled-land")
        replacement = self.add(game.engine, "Rest in Peace", seat="B")
        for card in (source, replacement):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)

        self.cast(game, "A", source)
        self.resolve_top(game)
        original_library = len(game.state.players["A"].zones["library"])
        self.assertIn(land.ref, self.offered(game)["legal_refs"])
        self.choose(game, [land])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[land.object_id].zone)
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("+1/+1"))
        self.assertEqual(original_library - 1, len(game.state.players["A"].zones["library"]))
        self.replay(game, load=True)
