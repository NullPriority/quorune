"""The current turn gates both original Gruul Spellbreaker recipients."""

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.player_target_protection import ControllerTurnPlayerHexproofHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Gruul Spellbreaker"
PROFILE = "commander_review"


class ControllerTurnPlayerAndSourceHexproofTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-turn-hexproof.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-turn-player-and-source-hexproof-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current turn and source Hexproof", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any(item.provenance.get("template_id") == "riot-linked-entry-choice-v1" for item in programs))
        grant, = [item for item in programs if item.event == "characteristics.evaluate"]
        return card, grant

    def test_complete_original_preserves_riot_trample_and_both_current_turn_grants(self):
        card, grant = self.program()
        player, self_grant = grant.handlers
        self.assertEqual("hexproof", ControllerTurnPlayerHexproofHandler().validate(player))
        self.assertEqual(player["source_condition"], self_grant["source_condition"])
        self.assertEqual("controller_turn", player["source_condition"]["kind"])
        node = FixedPublicStateCharacteristicsHandler().validate(self_grant)
        self.assertEqual(("source", ("Hexproof",)), (node.target_kind, node.abilities))
        original = compile_oracle_card(card, capability_registry=self.registry)
        trample, = [entry for face in original.faces for entry in face.nodes if entry.template_id == "printed-keyword-list-v1"]
        self.assertEqual("Trample", trample.text)
        for changed in (
            card.oracle_text.replace("During your turn", "During your opponents' turns"),
            card.oracle_text.replace("have hexproof", "have shroud"),
            card.oracle_text.replace("this creature", "creatures you control", 1),
            card.oracle_text.replace("you and this creature", "your teammates and this creature"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=changed), capability_registry=self.registry).status)

    def test_raw_turn_pair_rejects_bare_player_hexproof_and_altered_self_grant(self):
        _, grant = self.program()
        player, self_grant = grant.handlers
        alternate = {**player["source_condition"], "kind": "controller_is_monarch"}
        bare = {key: value for key, value in player.items() if key != "source_condition"}
        bare["handler_id"] = "ability.static.controller-player-target-protection.v1"
        with self.assertRaisesRegex(SemanticNodeError, "requires the current controller"):
            replace(grant, handlers=({**player, "source_condition": alternate}, self_grant))
        for malformed in (
            (self_grant,),
            (bare, self_grant),
            (player, {**self_grant, "source_condition": alternate}),
            (player, {**self_grant, "modifier": {**self_grant["modifier"], "add_abilities": ["Haste"]}}),
        ):
            with self.subTest(handlers=len(malformed), first=malformed[0]):
                result = bind_semantic_program_runtime(
                    replace(grant, handlers=malformed), capability_registry=self.registry, profile=PROFILE,
                )
                self.assertFalse(result["strict"], result)
                self.assertTrue(any("runtime_controller_turn_player_and_source_hexproof:invalid:" in reason
                                    for reason in result["blockers"]), result)

    def riot_entry(self, game, source, *, haste):
        self.cast_for(game, "A", source)
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        packet = game.packet("pilot:A", full=True)
        options = packet["decision"]["ctx"]["options"]
        option, = [entry["id"] for entry in options if entry["id"].startswith("decline:") == haste]
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        command = {"action_id": "choose", "choices": {"replacement": option}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.settle(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual(0 if haste else 1, current.counters.get("+1/+1", 0))
        keywords = game.engine._combat_keywords(current)
        self.assertEqual(haste, "haste" in keywords)
        self.assertIn("trample", keywords)

    def target_offer(self, game, seat, spell, *, available, protected):
        self.priority(game, seat)
        offer = self.action(game, seat, spell)
        refs = set(offer["target_schema"]["legal_refs"])
        self.assertTrue(set(available) <= refs, f"Current turn must offer {available}, got {sorted(refs)}")
        before = authoritative_state_hash(game.state)
        for ref in protected:
            self.assertNotIn(ref, refs)
            result = game.act("pilot:" + seat, {"action_id": offer["id"], "targets": [ref], "pay": "auto"})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))

    def turn_scene(self):
        game = self.session(70211039)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        borrow = self.add(engine, "Act of Treason", seat="B", zone="hand")
        b_shock = self.add(engine, "Shock", seat="B", zone="hand", ref="turn-hexproof-b-shock")
        c_shock = self.add(engine, "Shock", seat="C", zone="hand", ref="turn-hexproof-c-shock")
        other = self.add(engine, "Grizzly Bears")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="turn-hexproof-private")
        for index in range(4):
            self.add(engine, "Mountain", seat="B", ref=f"turn-hexproof-b-land-{index}")
        self.add(engine, "Mountain", seat="C", ref="turn-hexproof-c-land")
        for card in (source, borrow, b_shock):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 1, "G": 1})
        self.checkpoint(game)
        self.riot_entry(game, source, haste=True)
        self.target_offer(game, "B", b_shock, available=("C", "D", other.ref), protected=("A", source.ref))

        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.phase == "precombat_main" and current.state.pending_decision.kind == "priority")
        self.target_offer(game, "B", b_shock, available=("A", source.ref), protected=())
        self.assertIn("haste", engine._combat_keywords(game.state.cards[source.object_id]))
        return game, source, borrow, b_shock, c_shock, private

    def test_actual_current_turn_expires_then_control_change_protects_the_new_controller(self):
        game, source, borrow, b_shock, c_shock, private = self.turn_scene()
        self.cast_for(game, "B", borrow, [source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(("A", "B"), (current.owner, current.controller))
        self.target_offer(game, "C", c_shock, available=("A", "D"), protected=("B", current.ref))
        self.cast_for(game, "C", c_shock, ["A"])
        self.resolve_top(game)
        self.assertEqual(38, game.state.players["A"].life)
        self.cast_for(game, "B", b_shock, [current.ref])
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[source.object_id].marked_damage)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_turn_boundary_witness_kills_permanent_player_hexproof(self):
        with patch.object(ControllerTurnPlayerHexproofHandler, "applies_to_turn", return_value=True):
            with self.assertRaisesRegex(AssertionError, "Current turn must offer"):
                self.turn_scene()

    def test_actual_riot_counter_entry_keeps_the_independent_paid_branch(self):
        game = self.session(70211040)
        source = self.add(game.engine, NAME, zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "R": 1, "G": 1})
        self.checkpoint(game)
        self.riot_entry(game, source, haste=False)
