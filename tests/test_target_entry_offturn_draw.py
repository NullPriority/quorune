"""Targeted entry draw, private offer and committed off-turn draw usage."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_off_turn_draw import OFF_TURN_DRAW_CAPABILITY
from quorune.rules.target_entry_draw import TARGET_ENTRY_DRAW_CAPABILITY, target_entry_draw_schema
from quorune.rules.trigger_limits import TRIGGER_ONCE_PER_TURN_CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "Tataru Taru"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TargetEntryOffturnDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-entry-offturn-draw.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "target-entry-and-offturn-draw-cards.json", "player-target-protection-cards.json",
            "activation-result-views-cards.json", "bound-effect-program-cards.json",
            "event-player-fixed-damage-cards.json", "draw-rules-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target entry and off-turn draw", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_two_source_spans_have_independent_closed_authorities(self):
        card = self.db.lookup(CARD, fuzzy=False)
        synthetic = query_record(card.oracle_text.replace(CARD, "Generic Query Observer"))
        ir = compile_oracle_card(synthetic, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, treasure = ir.faces[0].nodes
        self.assertEqual(tuple(synthetic.oracle_text.splitlines()), tuple(
            synthetic.oracle_text[node.span.start:node.span.end] for node in (entry, treasure)))
        self.assertEqual(target_entry_draw_schema(), entry.target_schema)
        self.assertEqual("permanent.enter.self", entry.event)
        self.assertIn(TARGET_ENTRY_DRAW_CAPABILITY, entry.capability_dependencies)
        self.assertNotIn(OFF_TURN_DRAW_CAPABILITY, entry.capability_dependencies)
        self.assertIn(OFF_TURN_DRAW_CAPABILITY, treasure.capability_dependencies)
        self.assertIn(TRIGGER_ONCE_PER_TURN_CAPABILITY, treasure.capability_dependencies)
        for old, new in (
            ("target opponent may draw", "any opponent may draw"),
            ("if it isn't that player's turn", "if that player controls the moon"),
            ("draws a card", "puts a card into their hand"),
            ("tapped Treasure", "Treasure"),
        ):
            with self.subTest(new=new):
                altered = replace(synthetic, oracle_text=synthetic.oracle_text.replace(old, new, 1))
                result = compile_oracle_card(altered, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        for capability in (TARGET_ENTRY_DRAW_CAPABILITY, OFF_TURN_DRAW_CAPABILITY):
            matching, = (program for program in programs if capability in program.capability_dependencies)
            deps = [key for key in matching.capability_dependencies if key != capability]
            altered = replace(matching, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            rejected = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(rejected["strict"], rejected)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def treasures(self, game):
        return [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"
                and card.printed_name == "Treasure Token" and card.controller == "A"]

    def cast_source(self, game, source, opponent):
        self.assert_closed(game, source)
        self.cast(game, "A", source)
        self.assertEqual("semantic.target", self.resolve(game).kind)
        self.reject(game, "B", {"action_id": "choose", "targets": ["C"]})
        self.reject(game, "A", {"action_id": "choose", "targets": ["A"]})
        self.target(game, "A", [opponent])

    def test_paid_entry_offer_is_private_and_offturn_trigger_survives_source_departure(self):
        game = self.session(121603101)
        source = self.add(game.engine, CARD, zone="hand")
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, removal)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        count = {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.cast_source(game, source, "B")
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(count["A"] - 1, len(game.state.players["A"].zones["library"]))
        self.assertEqual(count["B"], len(game.state.players["B"].zones["library"]))
        draw = game.state.cards[game.state.players["A"].zones["hand"][-1]]
        for seat in "BCD":
            self.assertNotIn(draw.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        game = self.replay(game, load=True)
        self.reject(game, "A", {"action_id": "choose", "choice": "draw"})
        self.reject(game, "B", {"action_id": "choose", "choice": "draw", "count": 2})
        self.accept(game, "B", {"action_id": "choose", "choice": "draw"})
        self.assertEqual(count["B"] - 1, len(game.state.players["B"].zones["library"]))
        self.assertEqual(1, self.trigger_count(game, source))
        draw = game.state.cards[game.state.players["B"].zones["hand"][-1]]
        for seat in "ACD":
            self.assertNotIn(draw.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.cast(game, "B", removal, [source.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        token, = self.treasures(game)
        self.assertTrue(token.tapped)
        self.replay(game, load=True)

    def test_illegal_entry_target_cancels_the_whole_draw_sequence(self):
        game = self.session(121603102)
        source = self.add(game.engine, CARD, zone="hand")
        aegis = self.add(game.engine, "Aegis of the Gods", seat="B", zone="hand")
        orrery = self.add(game.engine, "Vedalken Orrery", seat="B")
        self.add(game.engine, "Plains", seat="B", ref="target-draw-first-plains")
        self.add(game.engine, "Plains", seat="B", ref="target-draw-second-plains")
        for card in (aegis, orrery):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        before = {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"}
        self.cast_source(game, source, "B")
        self.cast(game, "B", aegis)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[aegis.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, {seat: len(game.state.players[seat].zones["library"]) for seat in "ABCD"})
        self.assertEqual([], self.treasures(game))
        self.replay(game, load=True)

    def test_committed_draw_usage_is_once_before_resolution_and_resets_next_turn(self):
        game = self.session(121603103)
        source = self.add(game.engine, CARD)
        first = self.add(game.engine, "Sign in Blood", zone="hand", ref="offturn-draw-first")
        own = self.add(game.engine, "Sign in Blood", seat="B", zone="hand", ref="offturn-draw-owns-turn")
        next_draw = self.add(game.engine, "Sign in Blood", seat="B", zone="hand", ref="offturn-draw-next-turn")
        for i in range(4):
            self.add(game.engine, "Swamp", seat="B", ref=f"offturn-swamp-{i}")
        for card in (source, first, own, next_draw):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 2})
        self.checkpoint(game)
        self.cast(game, "A", first, ["B"])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual([], self.treasures(game))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.treasures(game)))
        self.until(game, lambda s: s.state.active_player == "B" and s.state.phase == "precombat_main"
                   and s.state.pending_decision.kind == "priority")
        self.cast(game, "B", own, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.treasures(game)))
        self.cast(game, "B", next_draw, ["C"])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self.treasures(game)))
        self.assertTrue(all(token.tapped for token in self.treasures(game)))
        self.replay(game, load=True)
