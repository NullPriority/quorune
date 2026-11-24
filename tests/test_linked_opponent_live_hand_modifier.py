"""Nyxathid applies one original selected player's live hand size in layer 7c."""

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
from quorune.characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope
from quorune.entry_designations import ENTRY_DESIGNATION_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Nyxathid"


class LinkedOpponentLiveHandModifierTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-opponent-hand.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-opponent-live-hand-modifier-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original selected opponent", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_entry_choice_and_exact_current_hand_modifier(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual({"zone.change", "continuous"}, {program.event for program in programs})
        self.assertTrue(all(program.target_schema is None for program in programs))
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", ir.status)
        for text in (
            card.oracle_text.replace("choose an opponent", "choose a player"),
            card.oracle_text.replace("chosen player's hand", "chosen player's library"),
            card.oracle_text.replace("-1/-1", "+1/+1"),
            card.oracle_text.split("\n", 1)[-1],
        ):
            denied = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", denied.status)

    def test_raw_linked_quantity_requires_its_paired_hand_scope_and_omission_gate(self):
        card = self.db.lookup(NAME, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, NAME)
        modifier, = [program for program in semantics.programs_for_oracle(card.oracle_id) if program.event == "continuous"]
        handler, = modifier.handlers
        value = handler["fragment"]["value"]
        expected = CharacteristicQuantitySpec(CharacteristicQuantityScope.LINKED_OPPONENT_HAND, schema_version=9)
        self.assertEqual(expected, CharacteristicQuantitySpec.from_dict(value["quantity"]))
        self.assertEqual((-1, -1), (value["power"], value["toughness"]))
        caps = [item for item in modifier.capability_dependencies if item != ENTRY_DESIGNATION_CAPABILITY]
        for altered in (
            replace(modifier, active_zone="all"),
            replace(modifier, capability_dependencies=caps,
                    capability_closure=self.registry.closure(caps, profile="commander_review").to_dict()),
        ):
            bound = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"], bound)
        for fields in ({"schema_version": 8}, {"offset": 1}, {"exclude_source": True}):
            with self.assertRaises(ValueError):
                CharacteristicQuantitySpec.from_dict({**expected.to_dict(), **fields})

    def stats(self, game, source):
        return tuple(game.engine._numeric_stat(source.object_id, value) for value in ("power", "toughness"))

    def select(self, game, source, seat):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        choice = game.packet("pilot:A", full=True)["decision"]
        offered = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in choice["ctx"]["options"]}
        self.assertEqual({"B", "C", "D"}, set(offered))
        self.assertNotEqual("battlefield", game.state.cards[source.object_id].zone)
        before = authoritative_state_hash(game.state)
        command = {"action_id": "choose", "choices": {"replacement": offered[seat]}}
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.settle(game)

    def test_actual_brainstorm_intermediate_hand_changes_do_not_run_state_based_actions_early(self):
        game = self.session(70430351)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        donate = self.add(engine, "Donate", zone="hand")
        brainstorm = self.add(engine, "Brainstorm", seat="B", zone="hand")
        self.add(engine, "Island", seat="B")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="nyxathid-hidden-card")
        player = game.state.players["B"]
        excess = len(player.zones["hand"]) - 6
        others = [uid for uid in player.zones["hand"] if uid != brainstorm.object_id]
        for object_id in others[:excess]:
            engine.move_card(object_id, "library", log=False)
        for card in (source, donate, brainstorm):
            self.assert_closed(game, card)
        self.assertEqual(6, len(player.zones["hand"]))
        self.prepare(game, mana={"C": 3, "B": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 2, "U": 1})
        self.select(game, source, "B")
        self.assertEqual((1, 1), self.stats(game, source), "The original selected opponent's hand supplies the count")

        prior = set(player.zones["hand"])
        self.cast_for(game, "B", brainstorm)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(8, len(player.zones["hand"]))
        self.assertEqual((-1, -1), self.stats(game, source))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        drawn = [game.state.cards[uid].ref for uid in player.zones["hand"] if uid not in prior]
        self.assertEqual(3, len(drawn))
        for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            for ref in drawn:
                self.assertNotIn(json.dumps(ref), json.dumps(packet))

        game = self.replay(game, load=True)
        command = {"action_id": "choose", "cards": [drawn[2], drawn[0]]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(6, len(game.state.players["B"].zones["hand"]))
        self.assertEqual(("battlefield", (1, 1)), (game.state.cards[source.object_id].zone, self.stats(game, source)))

        self.cast_for(game, "A", donate, ["B", source.ref])
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (game.state.cards[source.object_id].owner, game.state.cards[source.object_id].controller))
        self.assertEqual((1, 1), self.stats(game, source))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_hand_modifier_witness_kills_using_current_source_controller(self):
        with patch("quorune.rules.linked_entry_opponent.current_linked_entry_opponent",
                   side_effect=lambda host, source: source.controller):
            with self.assertRaisesRegex(AssertionError, "original selected opponent's hand supplies"):
                self.test_actual_brainstorm_intermediate_hand_changes_do_not_run_state_based_actions_early()

    def test_actual_chosen_player_departure_leaves_the_battlefield_source_and_ends_the_count(self):
        game = self.session(80040351)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        player = game.state.players["B"]
        for object_id in tuple(player.zones["hand"])[3:]:
            engine.move_card(object_id, "library", log=False)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.select(game, source, "B")
        self.assertEqual((4, 4), self.stats(game, source))
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual(("battlefield", (7, 7)), (game.state.cards[source.object_id].zone, self.stats(game, source)))
        self.replay(game, load=True)
