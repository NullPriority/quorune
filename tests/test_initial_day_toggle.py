from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.day_night import set_day_night_designation
from quorune.day_night_model import DAY_ENTRY_CAPABILITY, DAY_TOGGLE_COUNTER_CAPABILITY, DAY_TOGGLE_EVENT
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


CARD = "Vadrik, Astral Archmage"


class InitialDayToggleTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "initial-day-toggle.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "initial-day-toggle-cards", "attached-cast-restriction-cards",
            "fixed-spell-cost-reduction-cards", "next-spell-cost-reduction-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Initial day and toggles", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_actual_entry_establishes_day_without_counter_and_preserves_the_colored_floor(self):
        game = self.session(73110101)
        source = self.add(game.engine, CARD, zone="hand")
        draw = self.add(game.engine, "Divination", zone="hand")
        hidden = self.add(game.engine, "Plains", seat="C", zone="hand", ref="vadrik-private")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "U": 2, "R": 1})
        self.checkpoint(game)
        self.assertIsNone(game.state.day_night)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("day", game.state.day_night)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertNotIn("+1/+1", game.state.cards[source.object_id].counters)
        game = self.replay(game, load=True)

        self.priority(game, "A")
        offer = self.action(game, "A", draw)
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C":2}})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C":1, "U":1}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        for principal in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_turn_toggle_in_both_directions_uses_current_power_for_paid_instant(self):
        game = self.session(73110102)
        source = self.add(game.engine, CARD)
        draw = self.add(game.engine, "Opportunity", zone="hand")
        shocks = [self.add(game.engine, "Shock", seat="B", zone="hand", ref=f"vadrik-shock-{n}") for n in range(2)]
        for n in range(2):
            self.add(game.engine, "Island", ref=f"vadrik-blue-{n}")
            self.add(game.engine, "Mountain", seat="B", ref=f"vadrik-red-{n}")
        self.add(game.engine, "Plains", ref="vadrik-generic")
        self.assert_closed(game, source)
        self.assert_closed(game, draw)
        for seat in "AB":
            for object_id in tuple(game.state.players[seat].zones["hand"][:2]):
                game.engine.move_card(object_id, "graveyard", log=False, semantic_events=False)
        self.prepare(game)
        set_day_night_designation(game.engine, "day", reason="prepared established-day scenario")
        self.checkpoint(game)
        self.priority(game, "A")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn("cast:" + draw.ref, {row["id"] for row in legal})
        self.until(game, lambda session: session.state.day_night == "night" and bool(self.trigger_count(session, source)))
        self.assertEqual("B", game.state.active_player)
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual(2, game.engine._numeric_stat(source.object_id, "power"))
        self.until(game, lambda session: session.state.phase == "precombat_main" and session.state.active_player == "B")
        for shock in shocks:
            self.cast(game, "B", shock, ["D"])
            self.resolve_top(game)
        self.until(game, lambda session: session.state.day_night == "day" and bool(self.trigger_count(session, source)))
        self.assertEqual("C", game.state.active_player)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(3, game.engine._numeric_stat(source.object_id, "power"))
        self.cast(game, "A", draw, ["A"])
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_printed_entry_and_toggle_require_both_original_authorities(self):
        original = self.db.lookup(CARD, fuzzy=False)
        result = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        nodes = [node for face in result.faces for node in face.nodes]
        entry, = [node for node in nodes if node.event == "zone.change"]
        toggle, = [node for node in nodes if node.event == DAY_TOGGLE_EVENT]
        self.assertIn(DAY_ENTRY_CAPABILITY, entry.capability_dependencies)
        self.assertIn(DAY_TOGGLE_COUNTER_CAPABILITY, toggle.capability_dependencies)
        for old, new in (("neither day nor night", "already night"), ("day becomes night or night becomes day", "a new day begins")):
            changed = replace(original, oracle_text=original.oracle_text.replace(old, new, 1))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_original_toggle_is_killed(self):
        with patch("quorune.compiler.fixed_public_action_event_bindings.day_night_counter_binding", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_printed_entry_and_toggle_require_both_original_authorities()

    def test_omitting_the_initial_day_replacement_is_killed(self):
        with patch("quorune.compiler.private_entry_hand_templates.initial_day_entry_node", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_printed_entry_and_toggle_require_both_original_authorities()
