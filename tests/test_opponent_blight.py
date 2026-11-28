from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, pass_current
from quorune import CardDatabase, CommanderSession, GameConfig
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.opponent_blight import (
    OPPONENT_BLIGHT_BATCH_KIND,
    OPPONENT_BLIGHT_CAPABILITY,
    OpponentBlightBatchIntent,
    OpponentBlightSelection,
    opponent_blight_effect,
    validate_opponent_blight_effect,
)
from quorune.semantic_choices.apnap_commit import APNAP_OBJECT_COMMIT_OPERATION, ApnapObjectCommitHandler
from quorune.semantic_choices.intent_replacement import semantic_intent_identity, validate_semantic_intent_identity
from quorune.semantic_choices.model import SemanticChoiceError
from scripts.build_test_database import build_fixture_database


SOURCE = "High Perfect Morcant"


class OpponentBlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / "opponent-blight.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "affected-opponent-blight-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "counter-replacement-cards.json",
            fixtures / "counter-prohibition-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()

    def test_original_opponent_blight_binds_with_capability_and_keeps_activation(self):
        card = self.db.lookup(SOURCE, fuzzy=False)

        def compiled(registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        self.assertEqual("exact", compiled(self.registry).status)
        with patch("quorune.compiler.blight_templates.opponent_blight_entry_spec", return_value=None):
            residual = compiled(self.registry)
        self.assertTrue(residual.material_residuals)
        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in value["capabilities"] if row["id"] == OPPONENT_BLIGHT_CAPABILITY)
        capability.update(status="blocked", blockers=["test capability withdrawal"])
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        self.assertTrue(compiled(registry).material_residuals)

    def test_crossed_blight_and_sacrifice_descriptors_are_rejected(self):
        effect = opponent_blight_effect()
        with self.assertRaises(ValueError):
            validate_opponent_blight_effect({**effect, "then": "sacrifice"})
        with self.assertRaises(ValueError):
            validate_opponent_blight_effect({**effect, "schema_version": True})

        batch = OpponentBlightBatchIntent("A", "P1", (OpponentBlightSelection("B", "P2", "c2@1"),))
        kind, identity = semantic_intent_identity(batch)
        self.assertEqual(OPPONENT_BLIGHT_BATCH_KIND, kind)
        self.assertEqual(identity, validate_semantic_intent_identity(kind, identity))
        with self.assertRaises(SemanticChoiceError):
            validate_semantic_intent_identity("place_counter_batch", identity)
        with self.assertRaises(SemanticChoiceError):
            ApnapObjectCommitHandler().prepare({
                "op": APNAP_OBJECT_COMMIT_OPERATION,
                OPPONENT_BLIGHT_BATCH_KIND: identity,
                "destination": "graveyard",
            }, SimpleNamespace())

    def game(self):
        deck = DeckDefinition("Opposing-player Blight", [
            DeckEntry(SOURCE, 1, "commander"),
            DeckEntry("Generic Bound Plains", 16),
            DeckEntry("Elvish Warrior", 2),
            DeckEntry("Llanowar Elves"),
            DeckEntry("Doubling Season"),
            DeckEntry("Melira, Sylvok Outcast"),
        ], [SOURCE])
        game = CommanderSession.create(
            self.db, {seat: deck for seat in "ABCD"}, first_player="A",
            config=GameConfig(seed=392410, auto_pass_empty_priority=False),
        )
        keep_all(game)
        engine = game.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_passes = []
        return game

    def card(self, game, seat, name):
        return next(row for row in game.state.cards.values() if row.owner == seat and row.printed_name == name)

    def move_without_events(self, game, seat, name, zone="battlefield"):
        card = self.card(game, seat, name)
        game.engine.move_card(card.object_id, zone, controller=seat, semantic_events=False)
        return card

    def advance_to(self, game, kind):
        for _ in range(25):
            decision = game.state.pending_decision
            if decision is not None and decision.kind == kind:
                return
            self.assertIsNotNone(decision)
            self.assertEqual("priority", decision.kind)
            pass_current(game)
        self.fail(f"The {kind} decision did not open")

    def test_simultaneous_source_and_controlled_elf_entries_trigger_for_each(self):
        game = self.game()
        source = self.card(game, "A", SOURCE)
        ally = self.card(game, "A", "Llanowar Elves")
        enemy = self.card(game, "B", "Llanowar Elves")
        game.engine._move_cards_simultaneously(
            [(card.object_id, "battlefield") for card in (source, ally, enemy)],
            reason="fixture simultaneous linked arrivals",
        )
        triggers = [item for batch in game.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2, len(triggers))

    def test_actual_opponents_blight_simultaneously_and_replay(self):
        game = self.game()
        source = self.move_without_events(game, "A", SOURCE)
        elf = self.move_without_events(game, "A", "Llanowar Elves", "hand")
        self.move_without_events(game, "A", "Elvish Warrior")
        first = self.move_without_events(game, "B", "Elvish Warrior")
        second = self.move_without_events(game, "C", "Elvish Warrior")
        protected = self.move_without_events(game, "D", "Elvish Warrior")
        self.move_without_events(game, "B", "Doubling Season")
        self.move_without_events(game, "D", "Melira, Sylvok Outcast")
        engine = game.engine
        engine.state.players["A"].mana_pool["G"] = 1
        engine.state.priority_player = "A"
        engine._issue_priority("A", engine._priority_action_hints("A"))
        game.initial_checkpoint = checkpoint_envelope(game.state)
        game.commands.clear()
        game.decisions.clear()
        offered = next(row for row in engine._priority_action_hints("A")["actions"] if row.get("card") == elf.ref)
        accepted = game.act("pilot:A", {"action_id": offered["id"], "pay": "manual", "payment": {"G": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.advance_to(game, "choice.apnap")
        self.assertEqual(["pilot:B"], game.pending_principals())
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "cards": [second.ref]}).ok)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [second.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        first_result = game.act("pilot:B", {"action_id": "choose", "cards": [first.ref]})
        self.assertTrue(first_result.ok, first_result.summary)
        self.assertEqual(["pilot:C"], game.pending_principals())
        self.assertEqual(({}, {}), tuple(game.state.cards[row.object_id].counters for row in (first, second)))

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pending = root / "pending"
            game.save(pending)
            self.assertTrue(replay_record(pending, self.db, verify=True)["ok"])
            game = CommanderSession.load(self.db, pending)
            self.assertEqual(["pilot:C"], game.pending_principals())
            second_result = game.act("pilot:C", {"action_id": "choose", "cards": [second.ref]})
            self.assertTrue(second_result.ok, second_result.summary)
            self.assertEqual("priority", game.state.pending_decision.kind)
            self.assertEqual(2, game.state.cards[first.object_id].counters.get("-1/-1"))
            self.assertEqual(1, game.state.cards[second.object_id].counters.get("-1/-1"))
            self.assertEqual({}, game.state.cards[protected.object_id].counters)
            occurrences = [event for event in game.state.events
                           if event.code == "counter.add" and event.details.get("counter") == "-1/-1"]
            self.assertEqual(["B", "C"], [event.actor for event in occurrences])

            finished = root / "finished"
            game.save(finished)
            result = replay_record(finished, self.db, verify=True)
            self.assertTrue(result["ok"])
            self.assertEqual(authoritative_state_hash(game.state), result["final_state_hash"])
