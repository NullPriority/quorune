"""Native turn outcome, damage-result, and replay witnesses."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_match_outcome_rules as outcome_witnesses
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.match_outcome_restrictions import current_match_outcome_restrictions
from quorune.rules.turn_match_outcome_protection import (
    TURN_OUTCOME_CAPABILITY, turn_match_outcome_protection, validate_turn_match_outcome_protection,
)
from quorune.turn_match_outcome_model import TURN_OUTCOME_FIELD, ResolvedTurnMatchOutcomeProtection
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class TurnMatchOutcomeProtectionTests(InheritedNativeFixture, unittest.TestCase):
    begin_upkeep = outcome_witnesses.MatchOutcomeRulesTests.begin_upkeep
    witnessed_match = outcome_witnesses.MatchOutcomeRulesTests.witnessed_match

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Turn protection witnesses", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "turn-outcomes.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/turn-outcome-protection-cards.json", path)
        cls.use_database(path)

    def test_actual_protection_caps_damage_results_and_expires_after_life_loss(self):
        game = self.witnessed_match(10400270)
        commander_id, = game.state.players["A"].zones["command"]
        commander = game.engine.move_card(commander_id, "battlefield", controller="A", log=False)
        commander.acquired_control_turn_count = -1
        collar = self.add(game.engine, "Basilisk Collar")
        grace = self.add(game.engine, "Angel's Grace", seat="B", zone="hand")
        loss = self.add(game.engine, "Sign in Blood", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.add(game.engine, "Mountain")
        self.add(game.engine, "Swamp", ref="first-swamp")
        self.add(game.engine, "Swamp", ref="second-swamp")
        for card in (collar, grace, loss, bolt):
            self.assert_closed(game, card)
        receiver = game.state.players["B"]
        receiver.life = 1
        commander_key = commander.commander_designation_id
        self.assertIsNotNone(commander_key)
        receiver.commander_damage_received[commander_key] = 20
        before_lifelink = game.state.players["A"].life
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)

        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip = next(row for row in actions if row["id"].startswith("activate:" + collar.ref + ":"))
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [commander.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.attack(game, [(commander, "B")])
        self.cast_for(game, "B", grace)
        self.priority(game, "A")
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(bolt.ref, {row.get("card") for row in offered})
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[grace.object_id].zone)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(1, game.state.players["B"].life)
        self.assertEqual(22, game.state.players["B"].commander_damage_received[commander_key])
        self.assertEqual(before_lifelink + 2, game.state.players["A"].life)

        game = self.replay(game, load=True)
        self.cast_for(game, "A", loss, ["B"])
        self.resolve_top(game)
        self.assertEqual(-1, game.state.players["B"].life)
        self.cast_for(game, "A", bolt, ["B"])
        self.resolve_top(game)
        self.assertEqual(-4, game.state.players["B"].life)
        self.assertTrue(game.state.players["B"].in_game)
        self.assertEqual(frozenset({"B"}), current_match_outcome_restrictions(game.engine).loss_prohibited)
        self.until(game, lambda current: not current.state.players["B"].in_game)
        self.assertFalse(any(isinstance(rule, ResolvedTurnMatchOutcomeProtection) for rule in game.state.continuous_effects))
        self.replay(game, load=True)

    def test_opponents_remain_restricted_after_concession_until_last_player_wins(self):
        game = self.witnessed_match(10400271)
        source = self.add(game.engine, "Test of Endurance", seat="B")
        grace = self.add(game.engine, "Angel's Grace", zone="hand")
        private = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="private-turn-protection-card")
        self.add(game.engine, "Plains")
        game.state.players["B"].life = 50
        for card in (source, grace):
            self.assert_closed(game, card)
        self.begin_upkeep(game, "B")
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast_for(game, "A", grace)
        self.resolve_top(game)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
        self.priority(game, "A")
        result = game.act("pilot:A", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.assertEqual(frozenset({"B", "C", "D"}), current_match_outcome_restrictions(game.engine).win_prohibited)
        self.resolve_top(game)
        self.assertIsNone(game.state.winner)
        for seat in ("C", "D"):
            self.priority(game, seat)
            result = game.act("pilot:" + seat, {"action_id": "concede", "choices": {"confirm_concede": True}})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("B", game.state.winner)
        self.replay(game, load=True)

    def test_protection_requires_a_sealed_source_and_a_known_controller(self):
        instruction = turn_match_outcome_protection()
        validate_turn_match_outcome_protection(instruction)
        for change in ({"source": "$source"}, {"player": "$target.0"}, {TURN_OUTCOME_FIELD: True}, {"amount": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_turn_match_outcome_protection({**instruction, **change})
        game = self.witnessed_match(10400272)
        state = game.state.to_dict()
        state["continuous_effects"] = [ResolvedTurnMatchOutcomeProtection("OP0", "S0", 0, "missing-player").to_dict()]
        with self.assertRaisesRegex(ValueError, "unknown resolving player"):
            GameState.from_dict(state)

    def test_withholding_turn_protection_keeps_the_complete_original_residual(self):
        path = ROOT / "quorune/rules/capability-registry.json"
        value = json.loads(path.read_bytes())
        next(row for row in value["capabilities"] if row["id"] == TURN_OUTCOME_CAPABILITY).update(
            status="blocked", blockers=["Withdraw the turn outcome owner"],
        )
        registry = CapabilityRegistry(value)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=discovered)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        card = self.db.lookup("Angel's Grace", fuzzy=False)
        self.assertNotEqual("exact", compile_oracle_card(
            card, capability_registry=registry, capability_profile="commander_review",
        ).status)
        with patch("quorune.compiler.damage_permission_templates.turn_damage_prevention_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
