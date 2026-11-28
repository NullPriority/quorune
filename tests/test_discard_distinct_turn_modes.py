"""Turn-scoped mode usage is shared across actual controller discard occurrences."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_distinct_turn_trigger_modes as modal_witnesses
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.modal_selection import (
    DISTINCT_TURN_TRIGGER_MODES_CAPABILITY as CAPABILITY, DISTINCT_TURN_TRIGGER_MODES_FIELD,
)
from scripts.build_test_database import build_fixture_database


CARD = "Monument to Endurance"
PROFILE = "commander_review"
MODES = ["mode_1", "mode_2", "mode_3"]


class DiscardDistinctTurnModesTests(InheritedNativeFixture, unittest.TestCase):
    _order = modal_witnesses.DistinctTurnTriggerModeTests._order
    _assert_modes = modal_witnesses.DistinctTurnTriggerModeTests._assert_modes
    _choose = modal_witnesses.DistinctTurnTriggerModeTests._choose
    _reject = modal_witnesses.DistinctTurnTriggerModeTests._reject
    _mode_events = modal_witnesses.DistinctTurnTriggerModeTests._mode_events

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "discard-distinct-turn-modes.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "counted-spell-additional-costs.json",
            fixtures / "mana-discard-payment-cards.json",
            fixtures / "activation-public-state-cards.json",
            fixtures / "discard-distinct-turn-modes-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Discard distinct turn modes", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def discard_to_cast(self, game, seat, spell, *payments):
        self.priority(game, seat)
        offer = self.action(game, seat, spell)
        result = game.act(f"pilot:{seat}", {
            "action_id": offer["id"], "discard_cards": [card.ref for card in payments], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)

    def next_turn(self, game, *, keep):
        reserved = {game.state.cards[card.object_id].ref for card in keep}
        previous = game.state.turn_sequence
        for _ in range(4):
            self.until(game, lambda current: (
                current.state.turn_sequence > previous
                or current.state.pending_decision.kind == "cleanup.discard"
            ))
            if game.state.turn_sequence > previous:
                return
            principal, = game.pending_principals()
            context = game.packet(principal, full=True)["decision"]["ctx"]
            cards = [row["id"] for row in context["hand"] if row["id"] not in reserved][:context["count"]]
            result = game.act(principal, {"action_id": "discard", "cards": cards})
            self.assertTrue(result.ok, result.summary)
        self.fail("The next turn did not begin")

    def test_actual_discard_occurrences_consume_modes_before_resolution_and_reset_next_turn(self):
        game = self.session(60337031)
        source = self.add(game.engine, CARD)
        batch = self.add(game.engine, "Cathartic Reunion", zone="hand")
        singles = tuple(self.add(game.engine, "Thrill of Possibility", zone="hand", ref=f"thrill-{index}")
                        for index in range(3))
        other = self.add(game.engine, "Thrill of Possibility", seat="B", zone="hand", ref="opposing-thrill")
        own_cost = tuple(self.add(game.engine, "Generic Bound Body", zone="hand", ref=f"discard-cost-{index}")
                         for index in range(5))
        other_cost = self.add(game.engine, "Generic Bound Growth", seat="B", zone="hand", ref="opposing-cost")
        for index in range(2):
            self.add(game.engine, "Mountain", ref=f"next-turn-mountain-{index}")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 3, "R": 3})
        game.state.players["B"].mana_pool.update({"C": 1, "R": 1})
        self.checkpoint(game)
        incarnation = source.logical_object_id

        self.discard_to_cast(game, "B", other, other_cost)
        self.assertFalse(game.state.pending_trigger_batches)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))

        self.discard_to_cast(game, "A", batch, *own_cost[:2])
        self._order(game, count=2)
        self._assert_modes(game, MODES)
        self._reject(game, ["mode_1"], seat="B")
        self._reject(game, ["mode_1", "mode_2"])
        self._choose(game, "mode_1")
        self._assert_modes(game, MODES[1:])
        self._reject(game, ["mode_1"])
        game = self.replay(game, load=True)
        self._assert_modes(game, MODES[1:])
        self._choose(game, "mode_3")
        self.assertEqual([1, 3], [row.amount for row in self._mode_events(game)])
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.resolve_top(game)
        self.assertEqual([40, 37, 37, 37], [game.state.players[seat].life for seat in "ABCD"])
        private_draw_id = game.state.players["A"].zones["library"][-1]
        self.resolve_top(game)
        private_ref = game.state.cards[private_draw_id].ref
        self.assertIn(private_ref, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(private_ref, json.dumps(game.packet(principal, full=True)))
        self.assertIsNone(self.resolve(game))

        self.discard_to_cast(game, "A", singles[0], own_cost[2])
        self._assert_modes(game, ["mode_2"])
        self._reject(game, ["mode_3"])
        self._choose(game, "mode_2")
        self.resolve_top(game)
        treasure, = (card for card in game.state.cards.values() if card.is_token)
        self.assertEqual(("Treasure Token", "A", "battlefield", False),
                         (treasure.printed_name, treasure.controller, treasure.zone, treasure.tapped))
        self.assertIsNone(self.resolve(game))

        self.discard_to_cast(game, "A", singles[1], own_cost[3])
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertFalse(game.state.pending_trigger_batches)
        records = self._mode_events(game)
        self.assertEqual([1, 3, 2], [row.amount for row in records])
        self.assertEqual({incarnation}, {row.object_incarnation for row in records})
        self.assertIsNone(self.resolve(game))
        game = self.replay(game, load=True)

        self.next_turn(game, keep=(singles[2], own_cost[4]))
        self.assertEqual("B", game.state.active_player)
        self.assertIn(singles[2].object_id, game.state.players["A"].zones["hand"])
        self.assertIn(own_cost[4].object_id, game.state.players["A"].zones["hand"])
        self.assertEqual([], self._mode_events(game))
        self.discard_to_cast(game, "A", singles[2], own_cost[4])
        self._assert_modes(game, MODES)
        self._choose(game, "mode_1")
        self.assertEqual([1], [row.amount for row in self._mode_events(game)])
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_exact_controller_discard_and_independently_withdrawn_mode_authority(self):
        record = self.db.lookup(CARD, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(record, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(("card.discarded", "battlefield"), (node.event, node.active_zone))
        self.assertEqual({"field": "previous_controller", "op": "eq", "value": "$source.controller"},
                         node.event_condition)
        self.assertEqual((0, len(record.oracle_text)), (node.span.start, node.span.end))
        self.assertEqual(1, node.target_schema[DISTINCT_TURN_TRIGGER_MODES_FIELD])
        self.assertIn(CAPABILITY, node.capability_dependencies)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPABILITY)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])
        for old, new in (("you discard", "an opponent discards"), ("a card,", "a nonland card,"),
                         ("Draw a card.", "Target player draws a card.")):
            changed = replace(record, oracle_text=record.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        with patch("quorune.rules.modal_selection._DISTINCT_MODE_OCCURRENCES",
                   {"creature.enter": {"field": "controller"}}):
            self.assertNotEqual("exact", compile_oracle_card(record, **options).status)
