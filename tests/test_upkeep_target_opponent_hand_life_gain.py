"""Native evidence for one targeted upkeep hand-count life result."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_scheduled_player_triggers as scheduled_witnesses


ORIGINAL = "Gerrard Capashen"


class UpkeepTargetOpponentHandLifeGainTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "upkeep-target-opponent-hand-life-gain.sqlite3"
        fixture = ROOT / "tests/fixtures/upkeep-target-opponent-hand-life-gain-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted hand size on upkeep", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertIn("activate", {program.event for program in programs})
        upkeep, = [program for program in programs if program.event == "step.begin"]
        return card, upkeep

    def test_original_keeps_both_abilities_and_one_opposing_hand_count_as_its_life_result(self):
        card, program = self.program()
        self.assertEqual("battlefield", program.active_zone)
        self.assertEqual({"zones": ["player"], "categories": ["player"], "count": 1,
                          "player_relation": "opponent"}, program.target_schema)
        effect, = program.effects
        self.assertEqual(("life", "$controller"), (effect["op"], effect["player"]))
        self.assertEqual(ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET_HAND_COUNT, schema_version=4),
                         ScalarEffectAmountSpec.from_dict(effect["delta"]))
        for changed in (
            card.oracle_text.replace("your upkeep", "each opponent's upkeep"),
            card.oracle_text.replace("target opponent", "target player"),
            card.oracle_text.replace("1 life for each card", "2 life for each card"),
            card.oracle_text.replace("hand.", "hand. Draw a card."),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, changed)

    def test_raw_program_cannot_widen_target_recipient_or_upkeep(self):
        _, program = self.program()
        effect, = program.effects
        for changed in (
            replace(program, event_condition=None),
            replace(program, target_schema={**program.target_schema, "player_relation": "any"}),
            replace(program, effects=({**effect, "player": "$target.0"},)),
            replace(program, effects=({**effect, "delta": 3},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def enter_upkeep(self, game, player):
        scheduled_witnesses.ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self, game, player)

    def choose_c(self, game, source):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.target", ["B"]), (decision.kind, decision.actors))
        schema = decision.payload_by_actor["B"]["target_schema"]
        self.assertEqual({"A", "C", "D"}, set(schema["legal_refs"]))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:B", {"action_id": "choose", "targets": ["B"]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.target(game, "B", ["C"])
        trigger = game.state.stack[-1]
        self.assertEqual((source.object_id, "B", ["C"]),
                         (trigger.source_object_id, trigger.controller, list(trigger.targets)))

    def test_actual_borrowed_source_gains_its_controllers_life_from_the_targets_updated_hand(self):
        game = self.session(50360801)
        source = self.add(game.engine, ORIGINAL)
        game.engine.change_control(source.object_id, "B", reason="Prepare borrowed upkeep source")
        ritual = self.add(game.engine, "Dark Ritual", seat="C", zone="hand")
        secret = self.add(game.engine, "Dark Ritual", seat="C", zone="hand", ref="gerrard-hidden-hand-row")
        self.add(game.engine, "Swamp", seat="C")
        for card in (source, ritual):
            self.assert_closed(game, card)
        self.enter_upkeep(game, "B")
        self.choose_c(game, source)
        initial_size = len(game.state.players["C"].zones["hand"])
        before = {seat: game.state.players[seat].life for seat in "ABCD"}
        game = self.replay(game, load=True)
        self.cast_for(game, "C", ritual)
        self.resolve_top(game)
        self.assertEqual(initial_size - 1, len(game.state.players["C"].zones["hand"]))
        self.resolve_top(game)
        self.assertEqual({**before, "B": before["B"] + initial_size - 1},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(secret.ref, str(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_empty_opponent_is_legal_and_departed_target_produces_no_life(self):
        for mode in ("empty", "departed", "owner_upkeep"):
            with self.subTest(mode=mode):
                game = self.session(50360810 + len(mode))
                source = self.add(game.engine, ORIGINAL)
                game.engine.change_control(source.object_id, "B", reason="Prepare distinct owner and controller")
                self.assert_closed(game, source)
                if mode == "empty":
                    for object_id in tuple(game.state.players["C"].zones["hand"]):
                        game.engine.move_card(object_id, "library", log=False)
                self.enter_upkeep(game, "A" if mode == "owner_upkeep" else "B")
                initial = game.state.players["B"].life
                if mode != "owner_upkeep":
                    self.choose_c(game, source)
                    if mode == "departed":
                        self.priority(game, "C")
                        ended = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
                        self.assertTrue(ended.ok, ended.summary)
                        self.assertFalse(game.state.players["C"].in_game)
                    self.resolve_top(game)
                else:
                    self.assertEqual(0, self.trigger_count(game, source))
                self.assertEqual(initial, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_actual_tap_activation_remains_available_only_while_the_original_is_attacking(self):
        game = self.session(50360830)
        source = self.add(game.engine, ORIGINAL)
        source.acquired_control_turn_count = -1
        blocker = self.add(game.engine, "Grizzly Bears", seat="B")
        for index in range(4):
            self.add(game.engine, "Plains", ref=f"gerrard-native-funding-{index}")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        prefix = "activate:" + source.ref + ":"
        self.assertFalse(any(row["id"].startswith(prefix) for row in actions))
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:A", {"action_id": "attack", "attackers": {source.ref: "B"}})
        self.assertTrue(attack.ok, attack.summary)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in actions if row["id"].startswith(prefix)]
        activated = game.act("pilot:A", {"action_id": action, "targets": [blocker.ref], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.resolve_top(game)
        self.assertTrue(game.state.cards[blocker.object_id].tapped)
        self.replay(game, load=True)
