from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
import test_announced_damage as allocation_controls

from quorune.announced_damage import (
    ANNOUNCED_DAMAGE_CONTEXT,
    TURN_DAMAGE_MAXIMUM,
    TURN_DAMAGE_MAXIMUM_CAPABILITY,
    VariableDamageAllocationSpec,
)
from quorune.carddb import CardDatabase
from quorune.damage_turn_history import maximum_committed_damage_this_turn
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence_storage import read_evidence_document
from quorune.turn_history import roll_turn_history
from quorune.turn_history_model import TurnHistory, TurnHistoryEvent
from scripts.build_test_database import build_fixture_database


class ImpactResonanceTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = allocation_controls.AnnouncedDamageTests.session
    add = allocation_controls.AnnouncedDamageTests.add
    prepare = allocation_controls.AnnouncedDamageTests.prepare
    checkpoint = allocation_controls.AnnouncedDamageTests.checkpoint
    cast = allocation_controls.AnnouncedDamageTests.cast
    resolve = allocation_controls.AnnouncedDamageTests.resolve
    replay = allocation_controls.AnnouncedDamageTests.replay
    action = allocation_controls.AnnouncedDamageTests.action
    actions = allocation_controls.AnnouncedDamageTests.actions
    binding = allocation_controls.AnnouncedDamageTests.binding
    reject = allocation_controls.AnnouncedDamageTests.reject
    private_replay = allocation_controls.AnnouncedDamageTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        path = Path(cls.temp.name) / "impact-resonance.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "any-number-target-cards.json",
            fixtures / "compound-damage-recipient-cards.json",
            fixtures / "ragavan-top-cast-cards.json",
            fixtures / "targeted-spell-control-cards.json",
            fixtures / "impact-resonance-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Impact Resonance witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_actual_largest_single_deal_locks_shares_even_after_more_damage_and_bounce(self):
        session = self.session(94846856)
        impact = self.add(session.engine, "Impact Resonance", zone="hand")
        quake = self.add(session.engine, "Earthquake", zone="hand")
        life_loss = self.add(session.engine, "Bump in the Night", zone="hand")
        response = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        bounced = self.add(session.engine, "Generic Bound Body", seat="B", ref="bounced-creature")
        remaining = self.add(session.engine, "Generic Bound Body", seat="D", ref="remaining-creature")
        damaged = self.add(session.engine, "Generic Bound Body", seat="B", ref="quake-creature")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand")
        for creature in (bounced, remaining):
            creature.counters["flying"] = 1
        session.state.players["B"].mana_pool.update(R=1, U=1)
        self.prepare(session, mana={"R": 2, "C": 3, "B": 1})
        self.checkpoint(session)
        self.assertTrue(self.binding(session, impact)["strict_capability_ready"])
        self.cast(session, "A", life_loss, ["B"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(37, session.state.players["B"].life)
        accepted = session.act("pilot:A", {"action_id": self.action(session, quake)["id"], "x": 2, "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.cards[damaged.object_id].marked_damage)
        self.assertEqual(35, session.state.players["B"].life)
        self.assertEqual(2, maximum_committed_damage_this_turn(session.state))

        action = self.action(session, impact)
        schema = action["target_schema"]
        self.assertEqual({"schema_version": 2, "total": 2}, schema["damage_allocation"])
        self.assertEqual((0, 2), (schema["groups"][0]["min"], schema["groups"][0]["max"]))
        self.assertEqual({bounced.ref, remaining.ref, damaged.ref}, set(schema["legal_refs"]))
        command = {"action_id": action["id"], "targets": [bounced.ref, remaining.ref], "pay": "auto"}
        shares = {bounced.ref: 1, remaining.ref: 1}
        self.reject(session, {**command, "damage_allocation": shares}, "pilot:C")
        self.reject(session, {**command, "x": 2, "damage_allocation": shares})
        self.reject(session, {**command, "damage_allocation": {bounced.ref: 2, remaining.ref: 0}})
        self.reject(session, {**command, "damage_allocation": {bounced.ref: 1, remaining.ref: 2}})
        self.reject(session, {**command, "targets": ["B"], "damage_allocation": {"B": 2}})
        self.reject(session, {**command, "targets": [bounced.ref, bounced.ref],
                              "damage_allocation": {bounced.ref: 2}})
        self.reject(session, command)
        result = session.act("pilot:A", {**command, "choices": {"damage_allocation": shares}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertEqual({"schema_version": 2, "total": 2, "shares": [1, 1]},
                         session.state.stack[-1].context[ANNOUNCED_DAMAGE_CONTEXT])
        for seat in "ABCD":
            public = session.packet("pilot:" + seat, full=True)["state"]["stack"][0]
            self.assertEqual(([bounced.ref, remaining.ref], [1, 1]),
                             (public["targets"], public["damage_shares"]))

        impact_stack_ref = session.state.stack[-1].ref
        self.cast(session, "B", response, ["A"])
        for _ in range(8):
            if session.state.cards[response.object_id].zone == "graveyard":
                break
            principal = session.pending_principals()[0]
            session.packet(principal, full=True)
            passed = session.act(principal, {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.assertEqual("graveyard", session.state.cards[response.object_id].zone)
        self.assertEqual(impact_stack_ref, session.state.stack[-1].ref)
        self.assertEqual(3, maximum_committed_damage_this_turn(session.state))
        self.cast(session, "B", bounce, [bounced.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[bounced.object_id].zone)
        self.assertEqual(1, session.state.cards[remaining.object_id].marked_damage)
        self.assertEqual("graveyard", session.state.cards[impact.object_id].zone)
        self.private_replay(session, private)

    def test_actual_zero_allocation_after_full_prevention_still_pays_and_resolves(self):
        session = self.session(94846857)
        impact = self.add(session.engine, "Impact Resonance", zone="hand")
        source = self.add(session.engine, "Pyrotechnics", zone="hand")
        prevention = self.add(session.engine, "Mending Hands", seat="B", zone="hand")
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        session.state.players["B"].mana_pool["W"] = 1
        self.prepare(session, mana={"R": 2, "C": 5})
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": self.action(session, source)["id"],
                                         "targets": ["B"], "damage_allocation": {"B": 4}, "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(session, "B", prevention, ["B"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(40, session.state.players["B"].life)
        self.assertEqual(0, maximum_committed_damage_this_turn(session.state))
        action = self.action(session, impact)
        schema = action["target_schema"]
        self.assertEqual({"schema_version": 2, "total": 0}, schema["damage_allocation"])
        self.assertEqual((0, 0), (schema["groups"][0]["min"], schema["groups"][0]["max"]))
        command = {"action_id": action["id"], "pay": "auto"}
        self.reject(session, {**command, "targets": [target.ref], "damage_allocation": {target.ref: 1}})
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertEqual({"schema_version": 2, "total": 0, "shares": []},
                         session.state.stack[-1].context[ANNOUNCED_DAMAGE_CONTEXT])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(0, session.state.cards[target.object_id].marked_damage)
        self.assertEqual("graveyard", session.state.cards[impact.object_id].zone)
        self.replay(session, load=True)

    def test_amount_reads_individual_results_and_refuses_missing_or_stale_history(self):
        state = SimpleNamespace(turn_history=None, turn_sequence=3)
        self.assertIsNone(maximum_committed_damage_this_turn(state))
        spec = VariableDamageAllocationSpec(quantity=TURN_DAMAGE_MAXIMUM)
        with self.assertRaisesRegex(ValueError, "history is unavailable"):
            spec.bind(SimpleNamespace(state=state), None, "A", None)
        same_source = TurnHistoryEvent("player_damaged", actor="A", object_incarnation="source:1",
                                       target="B", target_kind="player", amount=4)
        state.turn_history = TurnHistory(turn_sequence=2, events=[
            same_source, replace(same_source, amount=3),
            TurnHistoryEvent("permanent_damaged", actor="C", object_incarnation="source:2",
                             target="battle", target_kind="permanent", target_object_incarnation="battle:1",
                             amount=5),
            TurnHistoryEvent("player_lost_life", target="B", target_kind="player", amount=8),
        ])
        self.assertIsNone(maximum_committed_damage_this_turn(state))
        state.turn_sequence = 2
        self.assertEqual(5, maximum_committed_damage_this_turn(state))
        state.turn_history = roll_turn_history(state.turn_history, next_turn_sequence=3, previous_active_player="A")
        state.turn_sequence = 3
        self.assertEqual(0, maximum_committed_damage_this_turn(state))
        self.assertEqual(spec, VariableDamageAllocationSpec.from_dict(spec.to_dict()))
        with self.assertRaises(ValueError):
            VariableDamageAllocationSpec.from_dict({"schema_version": 2, "amount": {
                "kind": TURN_DAMAGE_MAXIMUM, "increment": 1,
            }})
        with self.assertRaises(ValueError):
            VariableDamageAllocationSpec(quantity=TURN_DAMAGE_MAXIMUM, increment=1)

    def test_history_authority_withdrawal_leaves_a_material_original_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index

        registry_path = ROOT / "quorune/rules/capability-registry.json"
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        source = self.db.lookup("Impact Resonance")
        original = compile_oracle_card(source, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", original.status, original.material_residuals)
        self.assertIn(TURN_DAMAGE_MAXIMUM_CAPABILITY, original.faces[0].nodes[0].capability_dependencies)
        next(row for row in raw["capabilities"] if row["id"] == TURN_DAMAGE_MAXIMUM_CAPABILITY).update(
            status="blocked", blockers=["withdrawn cast-announced history amount"],
        )
        registry = CapabilityRegistry(raw)
        current_evidence = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        test_ids = {row["test_id"] for row in current_evidence["declarations"]}
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=test_ids,
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        revised = compile_oracle_card(source, capability_registry=registry, capability_profile="commander_review")
        reasons = [reason for item in revised.material_residuals for reason in item.blockers]
        self.assertIn(f"capability:status:{TURN_DAMAGE_MAXIMUM_CAPABILITY}:blocked", reasons)
        retained = compile_oracle_card(self.db.lookup("Rolling Thunder"), capability_registry=registry,
                                       capability_profile="commander_review")
        self.assertEqual("exact", retained.status, retained.material_residuals)

    def test_prevented_damage_or_zero_maximum_mutation_is_detected_by_native_witness(self):
        with patch("quorune.damage_turn_history.maximum_committed_damage_this_turn", return_value=4):
            with self.assertRaises(AssertionError):
                self.test_actual_zero_allocation_after_full_prevention_still_pays_and_resolves()
