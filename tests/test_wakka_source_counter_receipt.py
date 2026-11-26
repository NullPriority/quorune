"""Native CR 603.3d, 608.2b, and 122.6 evidence on the pinned Wakka card."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune import counter_placement
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import (
    current_plus_one_counter_placement_query,
    current_source_counter_receipt_query,
    matching_public_history_events,
    SOURCE_COUNTER_RECEIPT_CAPABILITY,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_history_model import PERMANENT_COUNTER_RECEIVED_EVENT, TurnHistory
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


WAKKA = "Wakka, Devoted Guardian"
RECEIPT_MODE = "permanent_counter_receipt_history_version"
SUPPORT_FIXTURES = {
    "activation-public-state-cards.json": (
        "Isamaru, Hound of Konda", "Plains", "Island", "Grizzly Bears", "Unsummon", "Vampire Hexmage",
    ),
    "occurrence-player-target-cards.json": ("Sol Ring",),
    "defending-player-target-cards.json": ("Phyrexian Walker",),
    "counter-placement-event-cards.json": ("Scar",),
    "turn-counter-placement-history-cards.json": ("Burst of Strength", "Cloudshift", "Solemnity"),
    "external-entry-counter-cards.json": ("Renata, Called to the Hunt",),
}


class WakkaSourceCounterReceiptTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        directory = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        cards, rulings = [], []
        for filename, names in SUPPORT_FIXTURES.items():
            raw = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            chosen = [row for row in raw["cards"] if row["name"] in names]
            if {row["name"] for row in chosen} != set(names):
                raise ValueError(f"Missing canonical Wakka witness cards in {filename}")
            card_ids = {row["oracle_id"] for row in chosen}
            cards.extend(chosen)
            rulings.extend(row for row in raw.get("rulings", ()) if row["oracle_id"] in card_ids)
        support = directory / "wakka-native-support.json"
        support.write_text(json.dumps({"schema_version": 1, "cards": cards, "rulings": rulings}), encoding="utf-8")
        path = directory / "wakka-native.sqlite3"
        build_fixture_database([fixtures / "wakka-source-counter-receipt-cards.json", support], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Counter receipt witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def scene(self, seed, *, from_hand=False):
        game = self.session(seed)
        wakka = self.add(game.engine, WAKKA, zone="hand" if from_hand else "battlefield", ref="wakka-source")
        wakka.acquired_control_turn_count = -1
        friend = self.add(game.engine, "Grizzly Bears", ref="wakka-friend")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="wakka-foreign-creature")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="wakka-private-d")
        self.add(game.engine, "Island", ref="wakka-active-priority-mana")
        self.add(game.engine, "Unsummon", zone="hand", ref="wakka-active-priority-spell")
        self.assert_closed(game, wakka)
        return game, wakka, friend, foreign, private

    def prepare_native(self, game, *, mana=(), opponent_mana=()):
        state = game.state
        state.config.semantic_policy = "trusted_only"
        state.config.auto_pass_empty_priority = True
        state.config.manual_active_main_phase = True
        state.players["A"].mana_pool.update(mana)
        state.players["B"].mana_pool.update(opponent_mana)
        game.engine.permissions.invalidate_current()
        state.pending_decision = None
        state.priority_player = None
        state.priority_passes = []
        state.active_player = "A"
        state.phase, state.step = "precombat_main", "main"
        state.phase_index = TURN_STEPS.index((state.phase, state.step))
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)

    def command(self, game, seat, payload):
        result = game.act("pilot:" + seat, payload)
        self.assertTrue(result.ok, result.summary)

    def advance(self, game, predicate):
        commands = {
            "priority": {"action_id": "pass"},
            "combat.attackers": {"action_id": "attack", "attackers": {}},
            "combat.blockers": {"action_id": "block", "blocks": {}},
        }
        for _ in range(50):
            if predicate(game):
                return
            decision = game.state.pending_decision
            self.assertIsNotNone(decision, game.state.step)
            self.assertIn(decision.kind, commands)
            self.command(game, decision.actors[0], commands[decision.kind])
        self.fail("Native turn progression did not reach Wakka's decision")

    def cast_top(self, game, seat, card, targets=()):
        self.cast_for(game, seat, card, list(targets))
        if game.state.stack:
            self.resolve_top(game)

    def receipt(self, game, card, *, incarnation=None):
        return matching_public_history_events(
            game.state,
            card.controller,
            current_source_counter_receipt_query(),
            source_incarnation=incarnation or game.state.cards[card.object_id].logical_object_id,
        )

    def finish_end_step(self, game, wakka, friend, foreign, *, triggered):
        self.advance(game, lambda current: current.state.step == "end_step")
        stack = [item for item in game.state.stack if item.source_object_id == wakka.object_id]
        self.assertEqual(int(triggered), len(stack), "Wakka end-step trigger count")
        if stack:
            self.resolve_top(game)
        self.assertEqual(int(triggered), game.state.cards[friend.object_id].counters.get("+1/+1", 0))
        self.assertEqual(0, game.state.cards[foreign.object_id].counters.get("+1/+1", 0))

    def test_pinned_card_closes_both_triggers_and_rejects_new_roles(self):
        game, wakka, *_ = self.scene(12269001)
        record = self.db.by_oracle_id(wakka.oracle_id)
        ir = compile_oracle_card(record, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        damage, end_step = (node for node in ir.faces[0].nodes if node.kind == "triggered_ability")
        self.assertEqual("$context.target", damage.target_schema["controller_seat"])
        self.assertEqual(1, damage.target_schema["up_to"])
        self.assertEqual(("destroy_targets", "place_counters"), tuple(effect["op"] for effect in damage.effects))
        self.assertEqual("$source.zone_object", damage.effects[1]["card"])
        self.assertIn(SOURCE_COUNTER_RECEIPT_CAPABILITY, end_step.capability_dependencies)
        self.assertTrue(end_step.effects[0]["set"]["exclude_source"])
        for text in (
            record.oracle_text.replace("that player controls", "another player controls"),
            record.oracle_text.replace("combat damage to a player", "combat damage to a planeswalker"),
            record.oracle_text.replace("counter was put on Wakka", "counter was put on a creature"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=text),
                                                            capability_registry=self.registry).status)
        programs = game.engine.semantics.programs_for_oracle(wakka.oracle_id)
        self.assertEqual(2, sum(program.event in {"damage.dealt.self", "step.begin"} for program in programs))
        end_step_program, = (program for program in programs if program.event == "step.begin")
        remaining = tuple(dep for dep in end_step_program.capability_dependencies if dep != SOURCE_COUNTER_RECEIPT_CAPABILITY)
        withdrawn = replace(end_step_program, capability_dependencies=remaining,
                            capability_closure=self.registry.closure(remaining, profile="commander_review").to_dict())
        rejection = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(rejection["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{SOURCE_COUNTER_RECEIPT_CAPABILITY}", rejection["blockers"])

    def test_actual_combat_targets_only_the_damaged_player_and_zero_targets_keep_the_counter(self):
        for offset, mode in enumerate(("destroy", "zero", "stale")):
            with self.subTest(result=mode):
                game, wakka, friend, foreign, private = self.scene(12269010 + offset)
                artifact = self.add(game.engine, "Phyrexian Walker", seat="B", ref="wakka-b-artifact")
                wrong = self.add(game.engine, "Sol Ring", seat="C", ref="wakka-c-artifact")
                if mode == "stale":
                    bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="wakka-target-bounce")
                    self.add(game.engine, "Island", seat="B", ref="wakka-target-bounce-island")
                self.prepare_native(game)
                self.advance(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
                self.command(game, "A", {"action_id": "attack", "attackers": {wakka.ref: "B"}})
                self.advance(game, lambda current: current.state.pending_decision.kind == "semantic.target")
                form = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
                self.assertEqual([artifact.ref], form["legal_refs"])
                self.assertEqual(36, game.state.players["B"].life)
                before = authoritative_state_hash(game.state)
                for seat, ref in (("A", wrong.ref), ("C", artifact.ref)):
                    self.assertFalse(game.act("pilot:" + seat, {"action_id": "choose", "targets": [ref]}).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                self.command(game, "A", {"action_id": "choose", "targets": [] if mode == "zero" else [artifact.ref]})
                if mode == "stale":
                    self.cast_top(game, "B", bounce, [artifact.ref])
                if game.state.stack:
                    self.resolve_top(game)
                self.assertEqual({"destroy": "graveyard", "zero": "battlefield", "stale": "hand"}[mode],
                                 game.state.cards[artifact.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[wrong.object_id].zone)
                self.assertEqual(int(mode != "stale"), game.state.cards[wakka.object_id].counters.get("+1/+1", 0))
                self.finish_end_step(game, wakka, friend, foreign, triggered=mode != "stale")
                self.replay(game)

    def received_then_removed(self, seed, *, blink=False):
        game, wakka, friend, foreign, _ = self.scene(seed)
        scar = self.add(game.engine, "Scar", seat="B", zone="hand", ref="wakka-foreign-counter")
        if blink:
            removal = self.add(game.engine, "Cloudshift", zone="hand", ref="wakka-blink")
        else:
            removal = self.add(game.engine, "Vampire Hexmage", seat="B", ref="wakka-remove-counters")
        self.prepare_native(game, mana={"W": 1}, opponent_mana={"B": 1})
        self.cast_top(game, "B", scar, [wakka.ref])
        original = game.state.cards[wakka.object_id].logical_object_id
        self.assertEqual(1, game.state.cards[wakka.object_id].counters["-1/-1"])
        if blink:
            self.cast_top(game, "A", removal, [wakka.ref])
            self.assertNotEqual(original, game.state.cards[wakka.object_id].logical_object_id)
        else:
            self.priority(game, "B")
            offer, = (row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith(f"activate:{removal.ref}:"))
            self.command(game, "B", {"action_id": offer["id"], "targets": [wakka.ref], "pay": "auto"})
            self.resolve_top(game)
        self.assertEqual({}, game.state.cards[wakka.object_id].counters)
        return game, wakka, friend, foreign, original

    def test_actual_other_players_counter_still_qualifies_after_removal_but_not_after_blink(self):
        for offset, blink in enumerate((False, True)):
            with self.subTest(blink=blink):
                game, wakka, friend, foreign, original = self.received_then_removed(12269020 + offset, blink=blink)
                game = self.replay(game, load=True)
                self.assertEqual([original], [event.object_incarnation for event in self.receipt(game, wakka, incarnation=original)])
                self.assertEqual(not blink, bool(self.receipt(game, wakka)))
                self.finish_end_step(game, wakka, friend, foreign, triggered=not blink)
                self.replay(game)

        with self.subTest(blink="after trigger"):
            game, wakka, friend, foreign, _ = self.scene(12269022)
            scar = self.add(game.engine, "Scar", seat="B", zone="hand", ref="queued-wakka-counter")
            blink = self.add(game.engine, "Cloudshift", zone="hand", ref="queued-wakka-blink")
            self.add(game.engine, "Plains", ref="queued-wakka-blink-mana")
            self.prepare_native(game, opponent_mana={"B": 1})
            self.cast_top(game, "B", scar, [wakka.ref])
            original = game.state.cards[wakka.object_id].logical_object_id
            self.advance(game, lambda current: current.state.step == "end_step")
            self.assertEqual([wakka.object_id], [item.source_object_id for item in game.state.stack])
            self.cast_top(game, "A", blink, [wakka.ref])
            self.assertNotEqual(original, game.state.cards[wakka.object_id].logical_object_id)
            self.assertEqual((), self.receipt(game, wakka))
            game = self.replay(game, load=True)
            if game.state.stack:
                self.resolve_top(game)
            self.assertEqual(1, game.state.cards[friend.object_id].counters.get("+1/+1", 0))
            self.assertEqual(1, game.state.cards[wakka.object_id].counters.get("+1/+1", 0))
            self.assertEqual(0, game.state.cards[foreign.object_id].counters.get("+1/+1", 0))
            self.replay(game)

    def test_actual_entry_placement_uses_the_new_incarnation_and_prevention_records_nothing(self):
        for offset, prevented in enumerate((False, True)):
            with self.subTest(prevented=prevented):
                game, wakka, friend, foreign, _ = self.scene(12269030 + offset, from_hand=True)
                self.add(game.engine, "Renata, Called to the Hunt", ref="wakka-entry-counter-source")
                if prevented:
                    self.add(game.engine, "Solemnity", seat="C", ref="wakka-counter-prevention")
                self.prepare_native(game, mana={"C": 2, "G": 1, "W": 1})
                old = wakka.logical_object_id
                self.cast_top(game, "A", wakka)
                live = game.state.cards[wakka.object_id]
                self.assertEqual("battlefield", live.zone)
                self.assertNotEqual(old, live.logical_object_id)
                self.assertEqual((), self.receipt(game, wakka, incarnation=old))
                self.assertEqual(not prevented, bool(self.receipt(game, wakka)))
                self.assertEqual(int(not prevented), live.counters.get("+1/+1", 0))
                self.finish_end_step(game, wakka, friend, foreign, triggered=not prevented)
                self.replay(game, load=True)

    def test_absent_legacy_mode_keeps_the_placing_controller_log_and_exact_replay(self):
        game = self.session(12269040)
        friend = self.add(game.engine, "Grizzly Bears", ref="legacy-receipt-subject")
        spell = self.add(game.engine, "Burst of Strength", seat="B", zone="hand", ref="legacy-placement")
        history = game.state.turn_history.to_dict()
        with self.assertRaises(ValueError):
            TurnHistory.from_dict({**history, RECEIPT_MODE: True})
        game.state.turn_history = TurnHistory.from_dict({key: value for key, value in history.items() if key != RECEIPT_MODE})
        self.prepare_native(game, opponent_mana={"G": 1})
        self.cast_top(game, "B", spell, [friend.ref])
        self.assertIsNone(self.receipt(game, friend))
        self.assertNotIn(RECEIPT_MODE, game.state.turn_history.to_dict())
        events = matching_public_history_events(game.state, "B", current_plus_one_counter_placement_query())
        self.assertEqual([(friend.logical_object_id, 1)], [(event.object_incarnation, event.amount) for event in events])
        self.replay(game, load=True)

    def test_omitting_receipts_breaks_the_actual_end_step_trigger(self):
        original = counter_placement._placement_history_for_commit

        def without_receipts(host, rows):
            turn, facts = original(host, rows)
            return turn, tuple(fact for fact in facts if fact.kind != PERMANENT_COUNTER_RECEIVED_EVENT)

        with patch.object(counter_placement, "_placement_history_for_commit", side_effect=without_receipts):
            game, wakka, friend, foreign, _ = self.received_then_removed(12269050)
            with self.assertRaisesRegex(AssertionError, "Wakka end-step trigger count"):
                self.finish_end_step(game, wakka, friend, foreign, triggered=True)
