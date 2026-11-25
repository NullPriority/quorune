"""Ingenious Prodigy's exact upkeep predicate and optional fixed counter payment."""

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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Ingenious Prodigy"


class UpkeepSourceCounterOptionalDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "upkeep-source-counter-optional-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/upkeep-source-counter-optional-draw-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Conditional upkeep counter payment", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return semantics.programs_for_oracle(card.oracle_id)

    def test_complete_original_keeps_the_counter_price_and_announced_entry(self):
        programs = self.programs()
        self.assertIn("zone.change", {program.event for program in programs})
        upkeep, = (program for program in programs if program.event == "step.begin")
        effect, = upkeep.effects
        self.assertIsNone(upkeep.target_schema)
        self.assertEqual(("remove_source_counter", "+1/+1", 1, "$source.zone_object"), tuple(
            effect["payment"][key] for key in ("kind", "counter_name", "amount", "source_ref")
        ))
        self.assertEqual(({"op": "draw", "player": "$controller", "count": 1, "private": True},),
                         tuple(dict(child) for child in effect["effects"]))

    def test_raw_upkeep_requires_the_intervening_counter_condition_and_original_price(self):
        upkeep, = (program for program in self.programs() if program.event == "step.begin")
        effect, = upkeep.effects
        for altered in (
            replace(upkeep, event="spell.cast"),
            replace(upkeep, event_condition=None),
            replace(upkeep, active_zone="graveyard"),
            replace(upkeep, effects=({**effect, "payment": {**effect["payment"], "counter_name": "charge"}},)),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_actual_announced_x_enters_with_two_counters_and_skulk(self):
        game = self.session(60340011)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        offer = self.action(game, "A", source)
        cast = game.act("pilot:A", {"action_id": offer["id"], "x": 2, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", 2), (current.zone, current.counters["+1/+1"]))
        self.assertIn("skulk", game.engine._combat_keywords(current))
        self.replay(game, load=True)

    def upkeep(self, game):
        def at_upkeep(current):
            return (current.state.active_player == "A" and current.state.step == "upkeep"
                    and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")

        for _ in range(len(game.state.turn_order) + 1):
            self.until(game, lambda current: at_upkeep(current) or (
                current.state.pending_decision is not None and current.state.pending_decision.kind == "cleanup.discard"
            ))
            if at_upkeep(game):
                return
            actor, = game.state.pending_decision.actors
            principal = f"pilot:{actor}"
            choice = game.packet(principal, full=True)["decision"]["ctx"]
            discarded = game.act(principal, {
                "action_id": "discard", "cards": [card["id"] for card in choice["hand"][:choice["count"]]],
            })
            self.assertTrue(discarded.ok, discarded.summary)
        self.fail("The source controller did not reach the next upkeep")

    def test_actual_upkeep_counter_choice_draws_privately_and_the_empty_source_stays_live(self):
        for mode in ("pay", "decline", "empty"):
            with self.subTest(mode=mode):
                game = self.session(60340021 + len(mode))
                source = self.add(game.engine, ORIGINAL)
                if mode != "empty":
                    source.counters["+1/+1"] = 2
                self.assert_closed(game, source)
                self.prepare(game, actor="D", active="D")
                self.checkpoint(game)
                self.upkeep(game)
                self.assertEqual(int(mode != "empty"), self.trigger_count(game, source))
                before = len(game.state.players["A"].zones["hand"])
                if mode != "empty":
                    choice = self.resolve(game)
                    self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
                    game = self.replay(game, load=True)
                    action = {"action_id": "choose", "pay": mode == "pay"}
                    unchanged = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:B", action).ok)
                    self.assertEqual(unchanged, authoritative_state_hash(game.state))
                    result = game.act("pilot:A", action)
                    self.assertTrue(result.ok, result.summary)
                current = game.state.cards[source.object_id]
                self.assertEqual("battlefield", current.zone)
                self.assertEqual({"pay": 1, "decline": 2, "empty": 0}[mode], current.counters.get("+1/+1", 0))
                self.assertEqual(before + (mode == "pay"), len(game.state.players["A"].zones["hand"]))
                if mode == "pay":
                    drawn = game.state.cards[game.state.players["A"].zones["hand"][-1]].ref
                    self.assertNotIn(drawn, json.dumps(game.packet("pilot:B", full=True)))
                self.replay(game, load=True)

    def test_actual_counter_removal_in_response_fails_the_upkeep_recheck_without_a_choice(self):
        game = self.session(60340041)
        source = self.add(game.engine, ORIGINAL)
        source.counters["+1/+1"] = 1
        hexmage = self.add(game.engine, "Vampire Hexmage", seat="B")
        self.assert_closed(game, source)
        self.assert_closed(game, hexmage)
        self.prepare(game, actor="D", active="D")
        self.checkpoint(game)
        self.upkeep(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith(f"activate:{hexmage.ref}:")]
        activated = game.act("pilot:B", {"action_id": action, "targets": [source.ref], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.resolve_top(game)
        self.assertEqual(("battlefield", 0), (game.state.cards[source.object_id].zone,
                                             game.state.cards[source.object_id].counters.get("+1/+1", 0)))
        game = self.replay(game, load=True)
        hand = tuple(game.state.players["A"].zones["hand"])
        self.resolve_top(game)
        self.assertEqual(hand, tuple(game.state.players["A"].zones["hand"]))
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.replay(game, load=True)
