"""Native witnesses for live outcome restrictions and intervening upkeep wins."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.match_outcome_restrictions import (
    MATCH_OUTCOME_RESTRICTION_CAPABILITY, current_match_outcome_restrictions,
)
from quorune.rules.upkeep_game_win import PLAYER_WIN_CAPABILITY
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


RESTRICTIONS = ("Platinum Angel", "Herald of Eternal Dawn", "Abyssal Persecutor", "Cloudsteel Kirin")
WINS = ("Test of Endurance", "Felidar Sovereign", "Epic Struggle", "Mortal Combat")


class MatchOutcomeRulesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Outcome witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "match-outcome.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/match-outcome-cards.json",
            ROOT / "tests/fixtures/fixed-extra-turn-end-step-loss-cards.json",
        ], path)
        cls.use_database(path)

    def witnessed_match(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        return game

    def begin_upkeep(self, game, player):
        engine = game.engine
        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.active_player = player
        game.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
        engine._enter_step()
        engine.pump()
        self.checkpoint(game)

    def test_both_static_relations_and_equipment_and_upkeep_originals_are_admitted(self):
        game = self.witnessed_match(10400230)
        for name in (*RESTRICTIONS, *WINS):
            with self.subTest(name=name):
                self.assert_closed(game, self.add(game.engine, name, zone="hand"))

    def test_protected_life_and_poison_losses_do_not_stop_concession_or_last_player_victory(self):
        for index, name in enumerate(RESTRICTIONS[:3]):
            with self.subTest(name=name):
                game = self.witnessed_match(10400231 + index)
                protector = self.add(game.engine, name, seat="A")
                self.assert_closed(game, protector)
                beneficiary = "B" if name == "Abyssal Persecutor" else "A"
                game.state.players[beneficiary].life = 2
                game.state.players[beneficiary].poison = game.state.config.poison_to_lose
                bolt = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand")
                self.assert_closed(game, bolt)
                self.prepare(game, actor="C", active="C", mana={"R": 1})
                self.checkpoint(game)
                self.cast_for(game, "C", bolt, [beneficiary])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(-1, game.state.players[beneficiary].life)
                self.assertTrue(game.state.players[beneficiary].in_game)
                game = self.replay(game, load=True)
                winner = "A" if beneficiary == "B" else "B"
                for seat in (beneficiary, "C", "D"):
                    self.priority(game, seat)
                    result = game.act("pilot:" + seat, {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertFalse(game.state.players[seat].in_game)
                self.assertEqual(winner, game.state.winner)
                self.replay(game, load=True)

    def test_protected_player_keeps_a_lethal_commander_damage_ledger(self):
        game = self.witnessed_match(10400236)
        protector = self.add(game.engine, "Platinum Angel", seat="B")
        self.assert_closed(game, protector)
        commander_id, = game.state.players["A"].zones["command"]
        commander = game.engine.move_card(commander_id, "battlefield", controller="A", log=False)
        commander.acquired_control_turn_count = -1
        receiver = game.state.players["B"]
        commander_key = commander.commander_designation_id
        self.assertIsNotNone(commander_key)
        receiver.commander_damage_received[commander_key] = 20
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(commander, "B")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(22, receiver.commander_damage_received[commander_key])
        self.assertTrue(receiver.in_game)
        self.replay(game, load=True)

    def test_protected_empty_draw_expires_and_only_a_fresh_attempt_causes_loss(self):
        game = self.witnessed_match(10400234)
        protector = self.add(game.engine, "Platinum Angel")
        first = self.add(game.engine, "Sign in Blood", seat="B", zone="hand")
        second = self.add(game.engine, "Vision Skeins", seat="B", zone="hand")
        shatter = self.add(game.engine, "Shatter", seat="B", zone="hand")
        for card in (protector, first, second, shatter):
            self.assert_closed(game, card)
        for object_id in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(object_id, "graveyard", log=False)
        self.prepare(game, actor="B", active="B", mana={"B": 2, "C": 2, "R": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", first, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.players["A"].in_game)
        self.assertFalse(game.state.players["A"].attempted_empty_draw)
        game = self.replay(game, load=True)
        self.cast_for(game, "B", shatter, [protector.ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.players["A"].in_game)
        self.cast_for(game, "B", second)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.players["A"].in_game)
        self.replay(game, load=True)

    def test_final_fortunes_resolved_loss_is_prevented_only_with_a_live_protector(self):
        for protected in (True, False):
            with self.subTest(protected=protected):
                game = self.witnessed_match(10400237 + protected)
                if protected:
                    self.assert_closed(game, self.add(game.engine, "Platinum Angel"))
                fortune = self.add(game.engine, "Final Fortune", zone="hand")
                self.assert_closed(game, fortune)
                self.prepare(game, mana={"R": 2})
                self.checkpoint(game)
                self.cast_for(game, "A", fortune)
                self.resolve_top(game)
                extra, = game.state.extra_turns
                self.until(game, lambda current: current.state.current_turn is not None
                           and current.state.current_turn.turn_id == extra.turn_id
                           and current.state.step == "end_step"
                           and bool(current.state.stack))
                trigger, = game.state.stack
                self.assertEqual([{"op": "lose_game", "player": "A"}], trigger.context["dynamic_effects"])
                self.resolve_top(game)
                self.assertEqual(protected, game.state.players["A"].in_game)
                self.replay(game, load=True)

    def test_equipment_grants_the_current_creature_an_ability_layer_six_can_remove(self):
        game = self.witnessed_match(10400235)
        equipment = self.add(game.engine, "Cloudsteel Kirin")
        bearer = self.add(game.engine, "Grizzly Bears")
        donate = self.add(game.engine, "Donate", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="D", zone="hand")
        dress_down = self.add(game.engine, "Dress Down", seat="C", zone="hand")
        for card in (equipment, donate, bolt, dress_down):
            self.assert_closed(game, card)
        game.state.players["B"].life = 1
        game.state.players["C"].mana_pool.update({"C": 1, "U": 1})
        game.state.players["D"].mana_pool["R"] = 1
        self.prepare(game, mana={"C": 7, "U": 1})
        self.checkpoint(game)
        legal_actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in legal_actions
                     if row["id"].startswith("activate:" + equipment.ref + ":")
                     and row["id"].endswith("-reconfigure-attach"))
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": [bearer.ref],
                                      "pay": "auto", "reserve": {"C": 2, "U": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(frozenset({"A"}), current_match_outcome_restrictions(game.engine).loss_prohibited)
        self.assertIn("flying", game.engine._combat_keywords(bearer))
        self.cast_for(game, "A", donate, ["B", bearer.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[equipment.object_id].controller)
        self.assertEqual(frozenset({"B"}), current_match_outcome_restrictions(game.engine).loss_prohibited)
        self.cast_for(game, "D", bolt, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.players["B"].in_game)
        game = self.replay(game, load=True)
        self.cast_for(game, "C", dress_down)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual("battlefield", game.state.cards[equipment.object_id].zone)
        self.replay(game, load=True)

    def test_an_upkeep_win_honors_the_prohibition_and_rechecks_its_intervening_life(self):
        for index, mode in enumerate(("prohibited", "source_removed", "threshold_lost", "controller_changed")):
            with self.subTest(mode=mode):
                game = self.witnessed_match(10400240 + index)
                name = "Test of Endurance" if mode in ("prohibited", "source_removed") else "Felidar Sovereign"
                source = self.add(game.engine, name, seat="B")
                self.assert_closed(game, source)
                game.state.players["B"].life = 50 if name == "Test of Endurance" else 40
                if mode in ("prohibited", "source_removed"):
                    protector = self.add(game.engine, "Platinum Angel")
                    self.assert_closed(game, protector)
                if mode == "source_removed":
                    answer = self.add(game.engine, "Shatter", seat="B", zone="hand")
                    self.assert_closed(game, answer)
                    game.state.players["B"].mana_pool.update({"C": 1, "R": 1})
                elif mode == "threshold_lost":
                    answer = self.add(game.engine, "Lightning Bolt", seat="A", zone="hand")
                    self.assert_closed(game, answer)
                    game.state.players["A"].mana_pool["R"] = 1
                elif mode == "controller_changed":
                    answer = self.add(game.engine, "Donate", seat="B", zone="hand")
                    self.add(game.engine, "Vedalken Orrery", seat="B")
                    self.assert_closed(game, answer)
                    game.state.players["B"].mana_pool.update({"C": 2, "U": 1})
                    game.state.players["A"].life = 39
                self.begin_upkeep(game, "B")
                self.assertEqual(1, self.trigger_count(game, source))
                if mode == "source_removed":
                    self.cast_for(game, "B", answer, [protector.ref])
                elif mode == "threshold_lost":
                    self.cast_for(game, "A", answer, ["B"])
                elif mode == "controller_changed":
                    self.cast_for(game, "B", answer, ["A", source.ref])
                self.assertIsNone(self.resolve(game))
                won = mode in ("source_removed", "controller_changed")
                self.assertEqual("B" if won else None, game.state.winner)
                self.assertEqual(won, game.state.game_over)
                if mode == "controller_changed":
                    self.assertEqual("A", game.state.cards[source.object_id].controller)
                self.replay(game, load=True)

    def test_current_creature_sets_use_the_controller_and_the_printed_zone(self):
        choices = (("Epic Struggle", "battlefield"), ("Mortal Combat", "graveyard"))
        for index, (name, zone) in enumerate(choices):
            with self.subTest(name=name):
                game = self.witnessed_match(10400250 + index)
                source = self.add(game.engine, name, seat="B")
                self.assert_closed(game, source)
                if zone == "battlefield":
                    game.engine.create_token(
                        "B", name="Creature Witness", quantity=20,
                        characteristics={"type_line": "Token Creature", "power": "1", "toughness": "1"},
                    )
                else:
                    records = json.loads((ROOT / "tests/fixtures/match-outcome-cards.json").read_bytes())["cards"]
                    creatures = [card["name"] for card in records
                                 if "Creature" in card["type_line"] and not card["oracle_text"]][:20]
                    self.assertEqual(20, len(creatures))
                    for creature in creatures:
                        self.add(game.engine, creature, seat="B", zone=zone)
                self.begin_upkeep(game, "B")
                self.assertEqual(1, self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("B", game.state.winner)
                self.replay(game, load=True)

    def test_exact_upkeep_life_and_hand_sizes_are_rechecked_without_revealing_cards(self):
        cases = (("Near-Death Experience", "life"), ("Triskaidekaphile", "hand"))
        for index, (name, resource) in enumerate(cases):
            for departed in (False, True):
                with self.subTest(name=name, departed=departed):
                    game = self.witnessed_match(10400260 + index * 2 + departed)
                    source = self.add(game.engine, name, seat="B")
                    private = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="private-threshold-card")
                    player = game.state.players["B"]
                    if resource == "life":
                        player.life = 1
                        answer = self.add(game.engine, "Revitalize", seat="B", zone="hand")
                        player.mana_pool.update({"C": 1, "W": 1})
                    else:
                        answer = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
                        player.mana_pool["R"] = 1
                        for number in range(13 - len(player.zones["hand"])):
                            self.add(game.engine, "Plains", seat="B", zone="hand", ref=f"threshold-land-{number}")
                    for card in (source, answer):
                        self.assert_closed(game, card)
                    self.begin_upkeep(game, "B")
                    self.assertEqual(1, self.trigger_count(game, source))
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
                    if departed:
                        self.cast_for(game, "B", answer, ["C"] if resource == "hand" else ())
                        self.resolve_top(game)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(None if departed else "B", game.state.winner)
                    self.replay(game, load=True)

    def test_withdrawal_or_omission_keeps_outcome_clauses_residual(self):
        registry_path = ROOT / "quorune/rules/capability-registry.json"
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        for capability, name, compiler in (
            (MATCH_OUTCOME_RESTRICTION_CAPABILITY, "Platinum Angel", "closed_static_nodes.compile_match_outcome_component"),
            (PLAYER_WIN_CAPABILITY, "Test of Endurance", "upkeep_game_win_nodes._current_win_condition"),
        ):
            with self.subTest(capability=capability):
                original = self.db.lookup(name)
                changed = json.loads(registry_path.read_bytes())
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Withhold outcome behavior"],
                )
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertNotEqual("exact", compile_oracle_card(
                    original, capability_registry=registry, capability_profile="commander_review",
                ).status)
                with patch("quorune.compiler." + compiler, return_value=None):
                    self.assertNotEqual("exact", compile_oracle_card(
                        original, capability_registry=self.registry, capability_profile="commander_review",
                    ).status)
                for text in ("Your teammate can't lose the game.", "At the beginning of their next upkeep, if you have 40 or more life, you win the game."):
                    altered = replace(original, oracle_text=text)
                    self.assertNotEqual("exact", compile_oracle_card(
                        altered, capability_registry=self.registry, capability_profile="commander_review",
                    ).status)
