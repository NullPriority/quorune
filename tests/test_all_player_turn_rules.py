"""Witness all-player turn rules through a trusted cast and saved game."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_match_outcome_rules as outcomes
from quorune.all_player_turn_rule_model import AllPlayerTurnRule, ResolvedAllPlayerTurnRule
from quorune.record import authoritative_state_hash
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.all_player_turn_rules import ALL_PLAYER_TURN_RULE_CAPABILITIES
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.match_outcome_restrictions import current_match_outcome_restrictions
from quorune.semantic_runtime.life_prohibitions import life_payment_prohibited
from quorune.semantic_runtime.player_target_protection import current_player_target_keywords
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class AllPlayerTurnRuleTests(InheritedNativeFixture, unittest.TestCase):
    begin_upkeep = outcomes.MatchOutcomeRulesTests.begin_upkeep
    witnessed_match = outcomes.MatchOutcomeRulesTests.witnessed_match

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "all-player-turn-rules.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/all-player-turn-rule-cards.json", db_path)
        outcomes.MatchOutcomeRulesTests.use_database.__func__(cls, db_path)

    def test_paid_original_protects_all_players_and_existing_creatures_through_replay(self):
        game = self.witnessed_match(6112501)
        protection = self.add(game.engine, "Everybody Lives!", zone="hand")
        first_creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="turn-rules-bear-b")
        second_creature = self.add(game.engine, "Grizzly Bears", seat="C", ref="turn-rules-bear-c")
        newcomer = self.add(game.engine, "Grizzly Bears", zone="hand", ref="turn-rules-late-bear")
        bolt = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand")
        gain = self.add(game.engine, "Revitalize", seat="D", zone="hand")
        confluence = self.add(game.engine, "Mana Confluence", seat="C")
        for object_id in tuple(game.state.players["D"].zones["library"]):
            game.engine.move_card(object_id, "graveyard", log=False)
        for card in (protection, bolt, gain, confluence):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1, "G": 1})
        game.state.players["C"].mana_pool["R"] = 1
        game.state.players["D"].mana_pool.update({"W": 1, "C": 1})
        self.checkpoint(game)

        self.cast_for(game, "A", protection)
        self.assertIsNone(self.resolve(game))
        for seat in "ABCD":
            self.assertIn("hexproof", current_player_target_keywords(game.engine, seat))
            self.assertTrue(life_payment_prohibited(game.engine, seat))
        for creature in (first_creature, second_creature):
            self.assertTrue({"hexproof", "indestructible"} <= set(game.engine._combat_keywords(creature)))
        self.assertNotIn(newcomer.ref, json.dumps(game.packet("pilot:C", full=True)))
        game = self.replay(game, load=True)
        rules = [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedAllPlayerTurnRule)]
        self.assertEqual(tuple(AllPlayerTurnRule), tuple(effect.rule for effect in rules))

        self.priority(game, "C")
        actions = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"].startswith("activate:" + confluence.ref) for row in actions))
        offer = next(row for row in actions if row["id"].startswith("cast:" + bolt.ref))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": offer["id"], "targets": ["B"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        life = game.state.players["C"].life
        self.cast_for(game, "C", bolt, ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life, game.state.players["C"].life)

        life = game.state.players["D"].life
        self.cast_for(game, "D", gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 3, game.state.players["D"].life)
        self.assertTrue(game.state.players["D"].in_game)
        self.assertFalse(game.state.players["D"].attempted_empty_draw)

        self.cast_for(game, "A", newcomer)
        self.assertIsNone(self.resolve(game))
        late = game.state.cards[newcomer.object_id]
        self.assertEqual("battlefield", late.zone)
        self.assertNotIn("hexproof", game.engine._combat_keywords(late))
        self.assertNotIn("indestructible", game.engine._combat_keywords(late))

        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertFalse(any(isinstance(effect, ResolvedAllPlayerTurnRule) for effect in game.state.continuous_effects))
        self.assertNotIn("hexproof", current_player_target_keywords(game.engine, "C"))
        self.assertFalse(life_payment_prohibited(game.engine, "C"))
        self.assertNotIn("indestructible", game.engine._combat_keywords(game.state.cards[second_creature.object_id]))
        self.replay(game, load=True)

    def test_resolving_rule_blocks_an_explicit_win_and_preserves_concession(self):
        game = self.witnessed_match(6112502)
        source = self.add(game.engine, "Test of Endurance", seat="B")
        spell = self.add(game.engine, "Everybody Lives!", zone="hand")
        for ref in ("turn-rule-plains-one", "turn-rule-plains-two"):
            self.add(game.engine, "Plains", ref=ref)
        game.state.players["B"].life = 50
        self.assert_closed(game, spell)
        self.begin_upkeep(game, "B")
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        active = frozenset("ABCD")
        rules = current_match_outcome_restrictions(game.engine)
        self.assertEqual(active, rules.win_prohibited)
        self.assertEqual(active, rules.loss_prohibited)
        self.priority(game, "A")
        concede = {"action_id": "concede", "choices": {"confirm_concede": True}}
        result = game.act("pilot:A", concede)
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.assertFalse(game.state.players["A"].in_game)
        self.resolve_top(game)
        self.assertIsNone(game.state.winner)
        for seat in "CD":
            self.priority(game, seat)
            result = game.act("pilot:" + seat, concede)
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("B", game.state.winner)
        self.replay(game, load=True)

    def test_every_rule_is_required_for_complete_admission(self):
        card = self.db.lookup("Everybody Lives!", fuzzy=False)
        options = {"capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(card, capability_registry=self.registry, **options).status)
        changed = replace(card, oracle_text=card.oracle_text.replace("Players gain hexproof", "Opponents gain hexproof", 1))
        self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry, **options).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        found = set(discover_tests(ROOT).values())
        for capability in ALL_PLAYER_TURN_RULE_CAPABILITIES.values():
            with self.subTest(capability=capability):
                value = deepcopy(raw)
                next(row for row in value["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Withhold this exact all-player rule"],
                )
                registry = CapabilityRegistry(value)
                index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=found)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry, **options).status)
        with patch("quorune.compiler.all_player_turn_rules.all_player_turn_rules_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry, **options).status)
