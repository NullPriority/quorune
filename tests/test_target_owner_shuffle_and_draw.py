"""The original nonland target keeps its owner through shuffle and draw."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import direct_permanent_target_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_controller_information import target_owner_reference
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Oblation"


class TargetOwnerShuffleAndDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "target-owner-shuffle-and-draw.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/target-owner-shuffle-and-draw-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target owner shuffle and draw", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_exact_nonland_target_and_two_owner_draws(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(("resolve", "stack"), (program.event, program.active_zone))
        expected = direct_permanent_target_spec("target nonland permanent").to_target_schema()
        self.assertEqual(expected, program.target_schema)
        shuffle, draw = program.effects
        self.assertEqual({"op": "shuffle_into_library", "card": "$target.0"}, shuffle)
        self.assertEqual(("draw", target_owner_reference(), 2), (draw["op"], draw["player"], draw["count"]))
        self.assertLessEqual({"resolution.target_owner_information", "target.revalidate_resolution",
                              "zone.draw.library_to_hand"}, set(program.capability_dependencies))
        for parts in (
            {"effects": [shuffle, {**draw, "count": 3}]},
            {"effects": [shuffle, {**draw, "player": "$controller"}]},
            {"effects": [shuffle]},
            {"target_schema": direct_permanent_target_spec("target permanent").to_target_schema()},
        ):
            with self.subTest(parts=parts):
                result = bind_semantic_program_runtime(
                    replace(program, **parts), capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(result["strict"], result)
        for text in (
            record.oracle_text.replace("owner", "controller"),
            record.oracle_text.replace("nonland permanent", "permanent"),
            record.oracle_text.replace("two cards", "three cards"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=text), capability_registry=self.registry,
            ).status)

    def shuffle_count(self, game, seat):
        return game.state.players[seat].stats.get("shuffle_count", 0)

    def test_actual_stolen_permanent_is_shuffled_before_its_owner_draws_from_the_short_library(self):
        game = self.session(70124221)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        steal = self.add(game.engine, "Act of Treason", zone="hand")
        target = self.add(game.engine, "Runeclaw Bear", seat="B", ref="oblation-stolen-target")
        land = self.add(game.engine, "Island", seat="B", ref="oblation-excluded-land")
        for object_id in tuple(game.state.players["B"].zones["library"][:-1]):
            game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
        for card in (source, steal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "R": 1, "W": 1})
        self.checkpoint(game)

        self.cast_for(game, "A", steal, [target.ref], reserve={"C": 2, "W": 1})
        self.resolve_top(game)
        live = game.state.cards[target.object_id]
        self.assertEqual(("B", "A"), (live.owner, live.controller))
        self.priority(game, "A")
        offer = self.action(game, "A", source)
        self.assertIn(target.ref, offer["target_schema"]["legal_refs"])
        self.assertNotIn(land.ref, offer["target_schema"]["legal_refs"])
        command = {"action_id": offer["id"], "targets": [target.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [land.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        a_hand, b_hand = (len(game.state.players[seat].zones["hand"]) for seat in ("A", "B"))
        a_shuffle, b_shuffle = (self.shuffle_count(game, seat) for seat in ("A", "B"))
        self.assertEqual(1, len(game.state.players["B"].zones["library"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertIn(target.object_id, game.state.players["B"].zones["hand"])
        self.assertEqual((a_hand, b_hand + 2),
                         tuple(len(game.state.players[seat].zones["hand"]) for seat in ("A", "B")))
        self.assertEqual((a_shuffle, b_shuffle + 1), tuple(self.shuffle_count(game, seat) for seat in ("A", "B")))
        self.assertEqual([], game.state.players["B"].zones["library"])
        self.replay(game, load=True)

    def test_actual_commander_replacement_preserves_the_owners_shuffle_and_draw_after_load(self):
        game = self.session(70124222)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        commander_id, = game.state.players["B"].zones["command"]
        commander = game.state.cards[commander_id]
        self.assert_closed(game, source)
        self.prepare(game, active="B", actor="B", mana={"W": 1})
        game.state.players["A"].mana_pool.update({"C": 2, "W": 1})
        self.checkpoint(game)
        self.cast(game, "B", commander)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[commander_id].zone)
        self.cast_for(game, "A", source, [commander.ref])

        before_hand = len(game.state.players["B"].zones["hand"])
        before_library = len(game.state.players["B"].zones["library"])
        before_shuffle = self.shuffle_count(game, "B")
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(before_shuffle, self.shuffle_count(game, "B"))
        self.assertEqual(before_hand, len(game.state.players["B"].zones["hand"]))
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        option, = [row["id"] for row in rows
                    if "commander" in (row["id"] + row.get("label", "")).casefold()
                    and not row["id"].startswith("decline:")]
        command = {"action_id": "choose", "replacement": option}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("command", game.state.cards[commander_id].zone)
        self.assertEqual(before_shuffle + 1, self.shuffle_count(game, "B"))
        self.assertEqual(before_hand + 2, len(game.state.players["B"].zones["hand"]))
        self.assertEqual(before_library - 2, len(game.state.players["B"].zones["library"]))
        self.replay(game, load=True)

    def test_actual_bounce_of_the_only_target_prevents_both_shuffle_and_draw(self):
        game = self.session(70124223)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        target = self.add(game.engine, "Runeclaw Bear", seat="B")
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (source, removal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", source, [target.ref])
        self.cast_for(game, "B", removal, [target.ref])
        self.resolve_top(game)

        before = {seat: (tuple(game.state.players[seat].zones["library"]), self.shuffle_count(game, seat),
                         len(game.state.players[seat].zones["hand"])) for seat in ("A", "B")}
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual(before, {seat: (tuple(game.state.players[seat].zones["library"]),
                                       self.shuffle_count(game, seat),
                                       len(game.state.players[seat].zones["hand"])) for seat in ("A", "B")})
        self.replay(game, load=True)
