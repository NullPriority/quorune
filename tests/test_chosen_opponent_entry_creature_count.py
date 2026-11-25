"""The chosen opponent's current creatures determine one prepared entry."""

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
from quorune.entry_designations import COUNTED_ENTRY_OPPONENT_DEPENDENCIES, LINKED_ENTRY_OPPONENT_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import entry_designations
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Canker Abomination"


class ChosenOpponentEntryCreatureCountTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-opponent-entry-count.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/chosen-opponent-entry-creature-count-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Selected opponent entry count", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_has_one_closed_nontargeted_entry_replacement(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(("zone.change", "all"), (program.event, program.active_zone))
        self.assertIsNone(program.target_schema)
        self.assertEqual(1, len(program.handlers))
        for changed in (
            card.oracle_text.replace("choose an opponent", "choose a player"),
            card.oracle_text.replace("choose an opponent", "choose target opponent"),
            card.oracle_text.replace("each creature", "each permanent"),
            card.oracle_text.replace("-1/-1", "+1/+1"),
        ):
            result = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", result.status)

    def test_raw_counted_choice_requires_its_entry_scope_and_count_authority(self):
        card = self.db.lookup(NAME, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, NAME)
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertLessEqual(set(COUNTED_ENTRY_OPPONENT_DEPENDENCIES), set(program.capability_dependencies))
        for dependency in COUNTED_ENTRY_OPPONENT_DEPENDENCIES:
            caps = [item for item in program.capability_dependencies if item != dependency]
            altered = replace(program, capability_dependencies=caps,
                              capability_closure=self.registry.closure(caps, profile="commander_review").to_dict())
            denied = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(denied["strict"], denied)
            self.assertTrue(any(dependency in blocker for blocker in denied["blockers"]))
        denied = bind_semantic_program_runtime(
            replace(program, active_zone="battlefield"), capability_registry=self.registry, profile="commander_review",
        )
        self.assertFalse(denied["strict"], denied)

    def entry_offer(self, game, source):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertNotEqual("battlefield", game.state.cards[source.object_id].zone)
        by_seat = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in packet["ctx"]["options"]}
        self.assertEqual({"B", "C", "D"}, set(by_seat))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertIsNone(game.packet(viewer, full=True)["decision"])
        return by_seat

    def select(self, game, source, seat):
        choices = self.entry_offer(game, source)
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "choices": {"replacement": choices[seat]}}
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.act("pilot:A", {"action_id": "pass"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        invalid = choices[seat].rsplit(":", 1)[0] + ":A"
        self.assertFalse(game.act("pilot:A", {**command, "choices": {"replacement": invalid}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.settle(game)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, aspect) for aspect in ("power", "toughness"))

    def test_actual_entry_uses_the_chosen_current_count_after_a_response_then_doubles_ordinary_counters(self):
        game = self.session(61412071)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        season = self.add(engine, "Doubling Season")
        self.add(engine, "Llanowar Elves", ref="canker-controller-creature")
        self.add(engine, "Grizzly Bears", seat="B", ref="canker-selected-bear")
        self.add(engine, "Ornithopter", seat="B", ref="canker-selected-artifact-creature")
        moved = self.add(engine, "Grizzly Bears", seat="B", ref="canker-leaving-selected")
        for index in range(4):
            self.add(engine, "Grizzly Bears", seat="C", ref=f"other-opponent-creature-{index}")
        self.add(engine, "Sol Ring", seat="B")
        self.add(engine, "Island", seat="B")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="canker-private-hand")
        for card in (source, season, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.cast_for(game, "B", bounce, [moved.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[moved.object_id].zone)

        self.select(game, source, "B")
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual({"-1/-1": 4}, current.counters, "Only the selected opponent's creatures determine the count")
        self.assertEqual((2, 2), self.stats(game, source))
        self.assertNotIn(LINKED_ENTRY_OPPONENT_FIELD, current.annotations)
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_entry_witness_kills_counting_the_entering_controller(self):
        original_count = entry_designations._controlled_entry_creatures

        def controller_count(host, seat):
            return original_count(host, "A")

        with patch.object(entry_designations, "_controlled_entry_creatures", controller_count):
            with self.assertRaisesRegex(AssertionError, "selected opponent's creatures determine the count"):
                self.test_actual_entry_uses_the_chosen_current_count_after_a_response_then_doubles_ordinary_counters()

    def test_actual_zero_creature_opponent_still_can_be_chosen_and_enters_without_counters(self):
        game = self.session(61412072)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        self.add(engine, "Grizzly Bears", seat="B")
        self.add(engine, "Ornithopter", seat="C")
        self.add(engine, "Sol Ring", seat="D")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.select(game, source, "D")
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual({}, current.counters)
        self.assertEqual((6, 6), self.stats(game, source))
        self.replay(game, load=True)
