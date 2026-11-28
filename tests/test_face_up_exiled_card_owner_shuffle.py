"""A current face-up exile target moves through its owner's canonical shuffle."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FaceUpExiledCardOwnerShuffleTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "face-up-exiled-card-owner-shuffle.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/face-up-exiled-card-owner-shuffle-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exiled card owner shuffle", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_has_one_face_up_exiled_card_and_one_owner_shuffle(self):
        admitted, semantics = register_card(self.db, self.registry, "Riftsweeper")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        record = self.db.lookup("Riftsweeper", fuzzy=False)
        trigger, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual("permanent.enter.self", trigger.event)
        self.assertEqual({"zones": ["exile"], "categories": ["card"], "count": 1, "face_down": False},
                         trigger.target_schema)
        self.assertEqual([{"op": "shuffle_into_library", "card": "$target.0"}], trigger.effects)
        for forged in (
            replace(trigger, target_schema={**trigger.target_schema, "face_down": True}),
            replace(trigger, event="step.begin"),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                forged, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        for altered in (
            record.oracle_text.replace("face-up exiled card", "card in exile"),
            record.oracle_text.replace("Its owner shuffles it into their library", "You shuffle it into your library"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=altered), capability_registry=self.registry,
                capability_profile="commander_review",
            ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def counts(self, game):
        return {seat: int(game.state.players[seat].stats.get("shuffle_count", 0)) for seat in "ABCD"}

    def begin_target(self, game, source):
        self.cast_for(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))

    def test_actual_owner_shuffles_once_after_source_leaves_and_face_down_card_is_illegal(self):
        game = self.session(70124001)
        source = self.add(game.engine, "Riftsweeper", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", zone="exile", seat="B")
        hidden = self.add(game.engine, "Shock", zone="exile", seat="B", ref="hidden-from-riftsweeper")
        target.known_to = ["A", "B", "C", "D"]
        hidden.face_down = True
        hidden.known_to = ["B"]
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        initial_counts = self.counts(game)
        initial_a = list(game.state.players["A"].zones["library"])
        initial_b = set(game.state.players["B"].zones["library"])
        self.begin_target(game, source)
        self.assertNotIn(hidden.printed_name, json.dumps(game.packet("pilot:A", full=True)))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:A", {"action_id": "choose", "targets": [hidden.ref]})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.target(game, "A", [target.ref])
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.current(game, source).zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("library", "B"), (self.current(game, target).zone, self.current(game, target).owner))
        self.assertEqual("exile", self.current(game, hidden).zone)
        self.assertEqual(initial_a, game.state.players["A"].zones["library"])
        self.assertEqual(initial_b | {target.object_id}, set(game.state.players["B"].zones["library"]))
        self.assertEqual({**initial_counts, "B": initial_counts["B"] + 1}, self.counts(game))
        self.assertNotIn(target.ref, json.dumps(game.packet("pilot:A", full=True)["state"]))
        self.replay(game, load=True)

    def test_actual_target_leaving_exile_in_response_prevents_owner_shuffle(self):
        game = self.session(70124002)
        source = self.add(game.engine, "Riftsweeper", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", zone="exile", seat="B")
        pull = self.add(game.engine, "Pull from Eternity", zone="hand", seat="B")
        for card in (source, pull):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 1})
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)
        initial_counts = self.counts(game)
        initial_b = list(game.state.players["B"].zones["library"])
        self.begin_target(game, source)
        self.target(game, "A", [target.ref])
        self.cast_for(game, "B", pull, [target.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, target).zone)
        self.assertEqual(initial_b, game.state.players["B"].zones["library"])
        self.assertEqual(initial_counts, self.counts(game))
        self.replay(game, load=True)

    def test_actual_suspended_card_loses_time_counters_when_its_owner_shuffles_it(self):
        game = self.session(70124003)
        source = self.add(game.engine, "Riftsweeper", zone="hand")
        suspended = self.add(game.engine, "Rift Bolt", zone="hand")
        for card in (source, suspended):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 1, "R": 1})
        self.checkpoint(game)
        initial_counts = self.counts(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row.get("action") == "suspend" and row.get("card") == suspended.ref)
        result = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        exiled = self.current(game, suspended)
        self.assertEqual(("exile", 1, False), (exiled.zone, exiled.counters.get("time"), exiled.face_down))
        original_identity = exiled.logical_object_id
        self.begin_target(game, source)
        self.target(game, "A", [suspended.ref])
        self.assertIsNone(self.resolve(game))
        moved = self.current(game, suspended)
        self.assertEqual("library", moved.zone)
        self.assertNotEqual(original_identity, moved.logical_object_id)
        self.assertNotIn("time", moved.counters)
        self.assertEqual({**initial_counts, "A": initial_counts["A"] + 1}, self.counts(game))
        self.replay(game, load=True)
