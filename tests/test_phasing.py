from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.continuous_effect_model import ContinuousEffectDuration
from quorune.continuous_effect_state import ResolutionEffectSource
from quorune.control_effects import gain_control_of_refs, source_continuity_snapshot
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.phasing_model import PHASING_CAPABILITY, PhasingRecord
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_assist_payment


class PhasingRuntimeTests(unittest.TestCase):
    Witness = test_assist_payment.AssistRuntimeTests
    register_fixture_cards_on_add = True
    session = Witness.session
    add = Witness.add
    prepare = Witness.prepare
    checkpoint = Witness.checkpoint
    replay = Witness.replay
    assert_normal_admission = Witness.assert_normal_admission

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "phasing.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "phasing-cards.json", "static-attached-control.json",
            "ability-kind-cost-modifier-cards.json", "spell-history-transformations-cards.json",
            "static-step-skip-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Phasing", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 16)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _stage_end_step(self, session, seat):
        engine = session.engine
        self.prepare(session, actor=seat, active=seat)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase, engine.state.step = "ending", "end_step"
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine.state.last_normal_turn_player = seat
        if engine.state.current_turn is not None:
            engine.state.current_turn = replace(engine.state.current_turn, player=seat)
        engine._grant_priority(seat)
        engine.pump()
        self.checkpoint(session)

    def _pass_end_step(self, session, next_seat):
        for _ in range(len(session.engine.active_seats)):
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(next_seat, session.state.active_player)

    def _assert_phased_before_untap(self, session, source):
        self.assertTrue(session.state.cards[source.object_id].phased_out, "source must phase before untap")
        self.assertTrue(session.state.cards[source.object_id].tapped, "phase-out precedes untapping")

    def _activation_offers(self, session, seat, card):
        action_rows = session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [action for action in action_rows if action["id"].startswith(f"activate:{card.ref}:")]

    def test_printed_phasing_requires_its_declared_lifecycle(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in raw["capabilities"] if row["id"] == PHASING_CAPABILITY)
        capability.update(status="blocked", blockers=["test Phasing withdrawal"])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified("0" * 64)
        for name in ("Breezekeeper", "Teferi's Isle"):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                exact = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", exact.status, exact.material_residuals)
                self.assertTrue(any(
                    PHASING_CAPABILITY in node.capability_dependencies for face in exact.faces for node in face.nodes
                ))
                blocked = compile_oracle_card(card, capability_registry=withdrawn, capability_profile="commander_review")
                self.assertNotEqual("exact", blocked.status)

    def test_actual_phasing_is_simultaneous_and_restores_actions_and_attachments(self):
        session = self.session(742101)
        engine = session.engine
        keeper = self.add(engine, "Breezekeeper")
        island = self.add(engine, "Teferi's Isle")
        ordinary = self.add(engine, "Generic Bound Plains")
        equipment = self.add(engine, "Bonesplitter", seat="B")
        private = session.state.cards[session.state.players["D"].zones["hand"][0]]
        attach_objects(session.state.cards, equipment, keeper, source_timestamp=equipment.zone_timestamp,
                       players=session.state.players)
        keeper.counters["+1/+1"] = 1
        for card in (keeper, island, ordinary):
            card.tapped = True
        for card in (keeper, island, equipment):
            self.assert_normal_admission(session, card)
        self._stage_end_step(session, "D")
        start = len(session.state.events)
        self._pass_end_step(session, "A")
        self.assertEqual("upkeep", session.state.step)
        for card in (keeper, island):
            self._assert_phased_before_untap(session, card)
            self.assertEqual("A", session.state.cards[card.object_id].phasing_record.return_seat)
        self.assertFalse(session.state.cards[ordinary.object_id].tapped)
        phase = [event for event in session.state.events[start:] if event.code == "untap.phasing"]
        self.assertEqual(1, len(phase))
        self.assertEqual({keeper.ref, island.ref, equipment.ref}, set(phase[0].details["out"]))
        self.assertEqual([], phase[0].details["in"])
        phased_equipment = session.state.cards[equipment.object_id]
        self.assertTrue(phased_equipment.phasing_record.indirect)
        self.assertEqual(keeper.logical_object_id, phased_equipment.phasing_record.attached_object.logical_object_id)
        self.assertEqual([], self._activation_offers(session, "A", island))
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": f"activate:{island.ref}:0"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)

        self._stage_end_step(session, "A")
        self._pass_end_step(session, "B")
        self.assertTrue(session.state.cards[equipment.object_id].phased_out)
        self.assertEqual([], self._activation_offers(session, "B", equipment))
        another = self.add(session.engine, "Sandbar Crocodile")
        another.tapped = True
        self.assert_normal_admission(session, another)
        self._stage_end_step(session, "D")
        self._pass_end_step(session, "A")
        self._assert_phased_before_untap(session, another)
        for initial in (keeper, island, equipment):
            current = session.state.cards[initial.object_id]
            self.assertEqual((initial.logical_object_id, initial.zone_timestamp, "battlefield"),
                             (current.logical_object_id, current.zone_timestamp, current.zone))
            self.assertFalse(current.phased_out)
            self.assertIsNone(current.phasing_record)
        self.assertEqual(keeper.object_id, session.state.cards[equipment.object_id].attached_to)
        self.assertEqual({"+1/+1": 1}, session.state.cards[keeper.object_id].counters)
        current_island = session.state.cards[island.object_id]
        self.assertFalse(current_island.tapped)
        offer, = self._activation_offers(session, "A", current_island)
        activated = session.act("pilot:A", {"action_id": offer["id"]})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual(2, session.state.players["A"].mana_pool.get("U"))
        self.replay(session, load=True)

    def test_actual_departed_controller_crosses_only_the_next_regular_seat_slot(self):
        session = self.session(742102)
        engine = session.engine
        keeper = self.add(engine, "Breezekeeper")
        aura = self.add(engine, "Mind Control", seat="B", zone="hand")
        for card in (keeper, aura):
            self.assert_normal_admission(session, card)
        engine.move_card(aura.object_id, "battlefield", aura_target_ref=keeper.ref)
        self.assertEqual("B", session.state.cards[keeper.object_id].controller)
        self._stage_end_step(session, "A")
        self._pass_end_step(session, "B")
        self.assertEqual(PhasingRecord("B"), session.state.cards[keeper.object_id].phasing_record)
        self.assertTrue(session.state.cards[aura.object_id].phasing_record.indirect)
        self._stage_end_step(session, "B")
        conceded = session.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertEqual("B", session.state.active_player)
        self.assertEqual("outside", session.state.cards[aura.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[keeper.object_id].zone)
        self._pass_end_step(session, "C")
        self.assertTrue(session.state.cards[keeper.object_id].phased_out)
        session = self.replay(session, load=True)

        stasis = self.add(session.engine, "Stasis", seat="D")
        self._stage_end_step(session, "A")
        self._pass_end_step(session, "C")
        delayed = session.state.cards[keeper.object_id]
        self.assertTrue(delayed.phased_out)
        self.assertTrue(delayed.phasing_record.departed_ready)
        session = self.replay(session, load=True)

        session.engine.move_card(stasis.object_id, "graveyard")
        self._stage_end_step(session, "C")
        self._pass_end_step(session, "D")
        returned = session.state.cards[keeper.object_id]
        self.assertFalse(returned.phased_out)
        self.assertEqual(("A", "A", "battlefield", keeper.logical_object_id),
                         (returned.owner, returned.controller, returned.zone, returned.logical_object_id))
        self.replay(session, load=True)

    def test_phased_custody_exiles_after_a_live_grant_expires_to_departed_default(self):
        session = self.session(742105)
        engine = session.engine
        keeper = self.add(engine, "Breezekeeper", zone="hand")
        grant = self.add(engine, "Generic Bound Body")
        engine.move_card(keeper.object_id, "battlefield", controller="B")
        gain_control_of_refs(
            engine, actor="A", object_refs=(keeper.ref,), controller="A",
            duration=ContinuousEffectDuration.UNTIL_SOURCE_LEAVES,
            source=ResolutionEffectSource(stack_ref="fixture:retained-control", object_id=grant.object_id,
                                          logical_object_id=grant.logical_object_id),
            history_snapshot=source_continuity_snapshot(grant), reason="fixture retained control",
        )
        self._stage_end_step(session, "D")
        self._pass_end_step(session, "A")
        self.assertEqual(PhasingRecord("A"), session.state.cards[keeper.object_id].phasing_record)
        passed = session.act("pilot:A", {"action_id": "pass"})
        self.assertTrue(passed.ok, passed.summary)
        conceded = session.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertTrue(session.state.cards[keeper.object_id].phased_out)
        self.replay(session, load=True)

        engine.move_card(grant.object_id, "graveyard")
        self.assertEqual("exile", session.state.cards[keeper.object_id].zone)
        self.assertFalse(session.state.cards[keeper.object_id].phased_out)
        self.assertIsNone(session.state.cards[keeper.object_id].phasing_record)

    def test_actual_timestamp_order_resume_preserves_completed_phasing(self):
        session = self.session(742106)
        engine = session.engine
        keeper = self.add(engine, "Breezekeeper")
        keeper.tapped = True
        first, second = (self.add(engine, "Sunlit Adept", ref=ref) for ref in ("adept-first", "adept-second"))
        first.active_face = second.active_face = "Sunlit Adept"
        engine.state.day_night = "day"
        self._stage_end_step(session, "D")
        self._pass_end_step(session, "A")
        self.assertEqual("permanent.timestamp_order", session.state.pending_decision.kind)
        self._assert_phased_before_untap(session, keeper)
        self.assertEqual("Sunlit Adept", session.state.cards[first.object_id].active_face)
        session = self.replay(session, load=True)
        chosen = session.act("pilot:A", {"action_id": "order", "cards": [second.ref, first.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("upkeep", session.state.step)
        self.assertEqual("Moonlit Adept", session.state.cards[first.object_id].active_face)
        self._assert_phased_before_untap(session, keeper)
        self.assertEqual(1, sum(event.code == "untap.phasing" for event in session.state.events))
        self.replay(session, load=True)

    def test_historical_phasing_pause_preserves_absent_format_and_pre_untap_state(self):
        session = self.session(742103)
        keeper = self.add(session.engine, "Breezekeeper")
        keeper.tapped = True
        session.state.phasing_lifecycle_version = None
        self._stage_end_step(session, "D")
        self._pass_end_step(session, "A")
        pause = session.engine._semantic_pause_annotation()
        self.assertEqual(("untap.phasing", "Breezekeeper"), (pause["event"], pause["label"]))
        self.assertFalse(session.state.cards[keeper.object_id].phased_out)
        self.assertTrue(session.state.cards[keeper.object_id].tapped)
        self.assertNotIn("phasing_lifecycle_version", session.state.to_dict())
        self.assertIsNone(self.replay(session, load=True).state.phasing_lifecycle_version)

    def test_native_phasing_detects_omitted_status_batch(self):
        session = self.session(742104)
        keeper = self.add(session.engine, "Breezekeeper")
        keeper.tapped = True
        self._stage_end_step(session, "D")
        with patch("quorune.phasing.commit_untap_phasing"):
            self._pass_end_step(session, "A")
        with self.assertRaisesRegex(AssertionError, "source must phase before untap"):
            self._assert_phased_before_untap(session, keeper)
