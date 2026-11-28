from __future__ import annotations

"""CR 724.1 and 514 through native resolution and the canonical cleanup owner."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.extra_turn_templates import FIXED_END_TURN_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class EndingTurnEffectTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Ending a turn",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ending-turn.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/ending-turn-effect-cards.json"], path)
        cls.use_database(path)

    def submit(self, game, seat, command, valid=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def assert_new_program(self, game, card):
        self.assert_closed(game, card)
        programs = game.engine.semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any(
            FIXED_END_TURN_CAPABILITY in program.capability_dependencies
            and any(effect.get("end_turn_version") == 1 for effect in program.effects)
            for program in programs
        ))

    def test_ending_the_turn_compiles_only_as_a_standalone_clause(self):
        card = self.db.lookup("Time Stop", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(card, **options).status)
        for text in (
            "Return all nonland permanents to their owners' hands. End the turn.",
            "Destroy all artifacts and creatures. End the turn.",
            "End the turn. Draw a card.",
        ):
            with self.subTest(text=text):
                result = compile_oracle_card(replace(card, oracle_text=text), **options)
                self.assertNotEqual("exact", result.status)
                self.assertTrue(result.material_residuals)

    def time_stop_setup(self, seed, *, max_hand_size=5):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        stop = self.add(game.engine, "Time Stop", seat="B", zone="hand")
        game.state.players["A"].max_hand_size = max_hand_size
        self.assert_new_program(game, stop)
        return game, stop

    def open_time_stop_priority(self, game):
        self.prepare(game, actor="A", active="A", mana={"C": 1, "U": 1, "W": 2, "G": 1})
        game.state.players["B"].mana_pool.update({"C": 4, "U": 2})
        self.checkpoint(game)

    def assert_cleanup_was_next(self, game, turn):
        self.assertEqual(("ending", "cleanup", "A"), (
            game.state.phase, game.state.step, game.state.active_player,
        ))
        self.assertEqual(turn, game.state.turn_sequence)
        self.assertFalse(game.state.stack)
        turn_events = [event for event in game.state.events if event.turn_sequence == turn]
        last_end = next(event for event in reversed(turn_events) if event.code == "turn.ended")
        boundaries = [event.summary for event in turn_events
                      if event.code == "step.begin" and event.event_id > last_end.event_id]
        self.assertEqual([f"{turn}:ending/cleanup."], boundaries)

    def test_time_stop_exiles_an_uncounterable_spell_and_runs_private_cleanup(self):
        game, stop = self.time_stop_setup(7241001)
        verdict = self.add(game.engine, "Supreme Verdict", zone="hand")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears")
        bear.marked_damage = 1
        private = self.add(game.engine, "Plains", zone="hand", ref="cleanup-private-land")
        self.open_time_stop_priority(game)
        turn = game.state.turn_sequence
        self.cast(game, "A", growth, [bear.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(bear.object_id, "toughness"))
        self.cast(game, "A", verdict)
        self.cast(game, "B", stop)
        self.resolve_top(game)

        self.assert_cleanup_was_next(game, turn)
        for card in (verdict, stop):
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        cleanup = game.state.pending_decision
        self.assertEqual("cleanup.discard", cleanup.kind)
        self.assertEqual(["pilot:A"], game.pending_principals())
        for seat in "BCD":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.assertEqual(1, game.state.cards[bear.object_id].marked_damage)
        self.assertEqual(5, game.engine._numeric_stat(bear.object_id, "toughness"))

        game = self.replay(game, load=True)
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        chosen = [row["id"] for row in ctx["hand"] if row["id"] != private.ref][:ctx["count"]]
        command = {"action_id": "discard", "cards": chosen}
        self.submit(game, "B", command, valid=False)
        self.submit(game, "A", command)
        self.assertEqual(5, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(0, game.state.cards[bear.object_id].marked_damage)
        self.assertEqual(2, game.engine._numeric_stat(bear.object_id, "toughness"))
        self.assertEqual("B", game.state.active_player)
        self.replay(game, load=True)

    def test_exiled_commander_state_based_choice_precedes_the_cleanup_discard(self):
        game, stop = self.time_stop_setup(7241002)
        commander = next(card for card in game.state.cards.values()
                         if card.zone == "command" and card.is_commander and card.owner == "A")
        self.open_time_stop_priority(game)
        self.cast(game, "A", commander)
        self.cast(game, "B", stop)
        self.resolve_top(game)

        self.assertEqual("exile", game.state.cards[commander.object_id].zone)
        self.assertFalse(game.state.stack)
        self.assertEqual("state.commander_zone", game.state.pending_decision.kind)
        self.assertEqual(("precombat_main", "main"), (game.state.phase, game.state.step))
        self.assertEqual(["pilot:A"], game.pending_principals())

        game = self.replay(game, load=True)
        command = {"action_id": "command", "choice": "command"}
        self.submit(game, "C", command, valid=False)
        self.submit(game, "A", command)
        self.assertEqual("command", game.state.cards[commander.object_id].zone)
        self.assertEqual("cleanup.discard", game.state.pending_decision.kind)
        self.assertEqual(("ending", "cleanup"), (game.state.phase, game.state.step))
        self.replay(game, load=True)

    def test_ending_combat_clears_attack_state_without_combat_damage(self):
        game, stop = self.time_stop_setup(7241006)
        bear = self.add(game.engine, "Grizzly Bears")
        bear.acquired_control_turn_count = -1
        for i in range(6):
            self.add(game.engine, "Island", seat="B", ref=f"time-stop-island-{i}")
        self.open_time_stop_priority(game)
        turn, life = game.state.turn_sequence, game.state.players["B"].life
        self.attack(game, [(bear, "B")])
        self.assertEqual("B", game.state.cards[bear.object_id].attacking)
        self.cast(game, "B", stop)
        self.resolve_top(game)
        self.assert_cleanup_was_next(game, turn)
        self.assertIsNone(game.state.cards[bear.object_id].attacking)
        self.assertFalse(game.state.combat.attackers)
        self.assertEqual(life, game.state.players["B"].life)
        self.replay(game, load=True)

    def sundial_setup(self, seed, active):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        sundial = self.add(game.engine, "Sundial of the Infinite")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_new_program(game, sundial)
        self.prepare(game, actor=active, active=active)
        game.state.players["A"].mana_pool["C"] = 1
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        return game, sundial, bolt

    def sundial_offers(self, game, sundial):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith("activate:" + sundial.ref + ":")]

    def test_sundial_keeps_the_own_turn_restriction_and_clears_an_opposing_spell(self):
        game, sundial, bolt = self.sundial_setup(7241003, "B")
        self.assertFalse(self.sundial_offers(game, sundial))
        self.assertFalse(game.state.cards[sundial.object_id].tapped)
        self.replay(game)

        game, sundial, bolt = self.sundial_setup(7241004, "A")
        self.cast(game, "B", bolt, ["C"])
        life = game.state.players["C"].life
        offer, = self.sundial_offers(game, sundial)
        command = {"action_id": offer["id"], "pay": "auto"}
        self.submit(game, "C", command, valid=False)
        self.submit(game, "A", command)
        self.assertTrue(game.state.cards[sundial.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual("exile", game.state.cards[bolt.object_id].zone)
        self.assertEqual(life, game.state.players["C"].life)
        self.assertEqual("battlefield", game.state.cards[sundial.object_id].zone)
        self.assertEqual("B", game.state.active_player)
        self.replay(game, load=True)

    def test_ending_turn_runtime_capability_cannot_be_omitted(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime

        game, stop = self.time_stop_setup(7241005)
        program, = (candidate for candidate in game.engine.semantics.programs_for_oracle(stop.oracle_id)
                    if FIXED_END_TURN_CAPABILITY in candidate.capability_dependencies)
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != FIXED_END_TURN_CAPABILITY)
        closure = self.registry.closure(dependencies, profile="commander_review").to_dict() if dependencies else None
        inadequate = replace(program, capability_dependencies=dependencies, capability_closure=closure)
        result = bind_semantic_program_runtime(inadequate, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + FIXED_END_TURN_CAPABILITY, result["blockers"])

    def test_verified_turn_end_withdrawal_reopens_both_complete_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == FIXED_END_TURN_CAPABILITY).update(
            status="blocked", blockers=["The exact turn-ending owner was withdrawn"],
            dependency_fail_closed_status="not_run",
        )
        withheld = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
                            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withheld.mark_evidence_verified(validate_capability_evidence_index(index, registry=withheld))
        for name in ("Time Stop", "Sundial of the Infinite"):
            with self.subTest(card=name):
                result = compile_oracle_card(self.db.lookup(name), capability_registry=withheld,
                                             capability_profile="commander_review")
                blockers = {item for residual in result.material_residuals for item in residual.blockers}
                self.assertTrue(any("status:" + FIXED_END_TURN_CAPABILITY + ":blocked" in item
                                    for item in blockers), blockers)
                self.assertFalse(any("evidence_index:unverified" in item for item in blockers))

    def test_skipping_the_turn_boundary_changes_the_native_result(self):
        with patch("quorune.turn_step_owner.TurnStepOwner.end_turn_now") as skipped:
            with self.assertRaisesRegex(AssertionError, "cleanup"):
                self.test_time_stop_exiles_an_uncounterable_spell_and_runs_private_cleanup()
        skipped.assert_called_once()
