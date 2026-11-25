"""Native source-color rule effects for current spells and controlled creatures."""

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
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.target_source import (
    TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY, TURN_SPELL_SOURCE_COLOR_PROTECTION_WINDOW,
)
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


class TurnSpellSourceColorProtectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        database = Path(cls.temporary.name) / "turn-spell-source-color-protection.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "turn-spell-source-color-protection-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Turn spell-source color protection", [
            DeckEntry(name, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [name])

    def _ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"C": 4, "U": 10, "B": 1})
        game.state.players["C"].mana_pool["U"] = 1
        self.prepare(game, mana={"R": 3, "G": 3, "U": 1, "C": 3})
        self.checkpoint(game)

    def _stack(self, game, card):
        return next(item.ref for item in game.state.stack if item.card_object_id == card.object_id)

    def _activate(self, game, seat, source, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        result = game.act("pilot:" + seat, {"action_id": action["id"], "targets": [target], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def _deny(self, game, seat, spell, target):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"] == "cast:" + spell.ref)
        self.assertNotIn(target, action["target_schema"]["groups"][0]["legal_refs"])
        self.assertIsNone(game.packet("pilot:D", full=True)["decision"])
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": action["id"], "targets": [target], "pay": "auto"})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_autumns_veil_counter_prohibition_reads_live_spell_color_and_allows_blue_ability(self):
        game = self.session(611_608_01)
        veil = self.add(game.engine, "Autumn's Veil", zone="hand", ref="AUT-COUNTER-VEIL")
        first = self.add(game.engine, "Lightning Bolt", zone="hand", ref="AUT-RED-FIRST")
        later = self.add(game.engine, "Lightning Bolt", zone="hand", ref="AUT-RED-SECOND")
        third = self.add(game.engine, "Lightning Bolt", zone="hand", ref="AUT-RED-THIRD")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="AUT-BLUE-COUNTER")
        recolored = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="AUT-RECOLORED-COUNTER")
        change = self.add(game.engine, "Moonlace", seat="C", zone="hand", ref="AUT-COLORLESS")
        ertai = self.add(game.engine, "Ertai, Wizard Adept", seat="B", ref="AUT-COUNTER-ABILITY")
        ertai.acquired_control_turn_count = -1
        self._ready(game, veil, first, counter, change, ertai)

        self.cast(game, "A", first, ["D"])
        original = self._stack(game, first)
        self.cast(game, "B", counter, [original])
        self.cast(game, "A", veil)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["D"].life)
        self.assertTrue(any(event.code == "stack.counter.failed" and event.details.get("stack") == original
                            for event in game.state.events))

        self.cast(game, "A", later, ["D"])
        self.cast(game, "B", recolored, [self._stack(game, later)])
        colorless = self._stack(game, recolored)
        self.cast(game, "C", change, [colorless])
        self.resolve_top(game)
        self.assertEqual([], game.engine._target_snapshot(colorless)["colors"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["D"].life)

        self.cast(game, "A", third, ["D"])
        self._activate(game, "B", ertai, self._stack(game, third))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_autumns_veil_targets_follow_current_creatures_and_allow_abilities_and_other_permanents(self):
        game = self.session(611_608_02)
        veil = self.add(game.engine, "Autumn's Veil", zone="hand", ref="AUT-TARGETING-VEIL")
        original = self.add(game.engine, "Grizzly Bears", ref="AUT-CREATURE-PRESENT")
        arrives = self.add(game.engine, "Grizzly Bears", zone="hand", ref="AUT-CREATURE-ARRIVES")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="AUT-UNPROTECTED-C")
        artifact = self.add(game.engine, "Sol Ring", ref="AUT-ARTIFACT")
        own_blue = self.add(game.engine, "Unsummon", zone="hand", ref="AUT-OWN-BLUE")
        blue = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="AUT-OPPONENT-BLUE")
        black = self.add(game.engine, "Doom Blade", seat="B", zone="hand", ref="AUT-BLACK")
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand", ref="AUT-ARTIFACT-BOUNCE")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B", ref="AUT-BLUE-ABILITY")
        pinger.acquired_control_turn_count = -1
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            if not game.state.cards[object_id].ref.startswith("AUT-"):
                ZoneTransitionOwner(game.engine).move_card(object_id, "library")
        self._ready(game, veil, original, own_blue, blue, black, bounce, pinger)
        turn = game.state.turn_sequence
        self.cast(game, "A", veil)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", arrives)
        self.assertIsNone(self.resolve(game))

        self._deny(game, "A", own_blue, original.ref)
        self._deny(game, "B", blue, arrives.ref)
        self._deny(game, "B", black, original.ref)
        self.cast(game, "B", bounce, [artifact.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[artifact.object_id].zone)
        self._activate(game, "B", pinger, original.ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[original.object_id].marked_damage)
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)

        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertNotIn(TURN_SPELL_SOURCE_COLOR_PROTECTION_WINDOW, game.state.players["A"].stats)
        self.replay(game, load=True)

    def test_autumns_veil_source_color_shape_rejects_widened_actors_and_scopes(self):
        game = self.session(611_608_03)
        card = self.add(game.engine, "Autumn's Veil")
        self.assert_closed(game, card)
        record = self.db.lookup("Autumn's Veil", fuzzy=False)
        program, = game.engine.semantics.programs_for_oracle(record.oracle_id)
        effect, = program.effects
        self.assertIn(TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY, program.capability_dependencies)
        self.assertEqual(["U", "B"], effect["colors"])
        for changed in (
            replace(program, effects=({**effect, "player": "$target.0"},)),
            replace(program, effects=({**effect, "schema_version": True},)),
            replace(program, effects=({**effect, "colors": ["U", "B", "R"]},)),
            replace(program, event="permanent.enter.self"),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        for old, new in (
            ("black spells this turn,", "red spells this turn,"),
            ("black spells this turn.", "black spells or abilities this turn."),
            ("creatures you control", "permanents you control"),
        ):
            changed = replace(record, oracle_text=record.oracle_text.replace(old, new, 1))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY)
        row.update(status="blocked", blockers=["source-color protection withdrawn"])
        self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=CapabilityRegistry(raw)).status)
        with patch("quorune.compiler.temporary_player_rule_templates.turn_spell_source_color_protection_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry).status)
