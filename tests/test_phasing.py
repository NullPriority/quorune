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


    def test_direct_phase_out_dispatch_retains_the_controller_schedule_and_attachments(self):
        from quorune.phasing_model import phase_out_effect

        session = self.session(742211)
        engine = session.engine
        keeper = self.add(engine, "Breezekeeper")
        equipment = self.add(engine, "Bonesplitter", seat="B")
        attach_objects(session.state.cards, equipment, keeper, source_timestamp=equipment.zone_timestamp,
                       players=session.state.players)
        keeper.counters["+1/+1"] = 1
        self.prepare(session, actor="C", active="C")
        initial = keeper.logical_object_id, keeper.zone_timestamp
        result = engine.apply_effect({**phase_out_effect(targeted=True), "card": keeper.ref}, actor="C")
        self.assertEqual(keeper.ref, result)
        self.assertTrue(keeper.phased_out)
        self.assertEqual(PhasingRecord("A"), keeper.phasing_record)
        self.assertTrue(equipment.phased_out, "indirect attachment must phase with its host")
        self.assertTrue(equipment.phasing_record.indirect)
        self.assertEqual(initial[0], equipment.phasing_record.attached_object.logical_object_id)
        self.assertEqual(initial, (keeper.logical_object_id, keeper.zone_timestamp))
        self._stage_end_step(session, "D")
        self._pass_end_step(session, "A")
        self.assertFalse(keeper.phased_out)
        self.assertFalse(equipment.phased_out)
        self.assertEqual(keeper.object_id, equipment.attached_to)
        self.assertEqual(initial, (keeper.logical_object_id, keeper.zone_timestamp))
        self.assertEqual(1, keeper.counters["+1/+1"])
        self.replay(session, load=True)

    def test_phase_out_effect_detects_an_omitted_indirect_attachment(self):
        from quorune.phasing import _outgoing_phasing

        def roots_only(host, battlefield, roots):
            return {oid: transition for oid, transition in _outgoing_phasing(host, battlefield, roots).items()
                    if oid in roots}

        with patch("quorune.phasing._outgoing_phasing", side_effect=roots_only):
            with self.assertRaisesRegex(AssertionError, "indirect attachment must phase"):
                self.test_direct_phase_out_dispatch_retains_the_controller_schedule_and_attachments()

    def test_native_phasing_detects_omitted_status_batch(self):
        session = self.session(742104)
        keeper = self.add(session.engine, "Breezekeeper")
        keeper.tapped = True
        self._stage_end_step(session, "D")
        with patch("quorune.phasing.commit_untap_phasing"):
            self._pass_end_step(session, "A")
        with self.assertRaisesRegex(AssertionError, "source must phase before untap"):
            self._assert_phased_before_untap(session, keeper)


class FixedPhaseOutInstructionTests(unittest.TestCase):
    def test_printed_single_subjects_require_their_phase_out_and_lifecycle_owners(self):
        from quorune.compiler.resolution_effect_templates import _fixed_phase_out_template
        from quorune.phasing_model import PHASE_OUT_CAPABILITY
        from quorune.rules.occurrence_object_results import phase_out_node_capabilities

        for printed, targeted in (
            ("This creature phases out.", False),
            ("Target creature phases out.", True),
            ("Target creature you control phases out.", True),
            ("Target artifact, creature, or land phases out.", True),
        ):
            with self.subTest(printed=printed):
                _template, effects, target, _mechanics = _fixed_phase_out_template(
                    printed, card_name="Fixture", source_is_permanent=True,
                )
                required = phase_out_node_capabilities(effects, target)
                self.assertIn(PHASE_OUT_CAPABILITY, required)
                self.assertIn(PHASING_CAPABILITY, required)
                self.assertEqual(targeted, "target.revalidate_resolution" in required)
        for printed in (
            "All creatures phase out.", "Target land phases out.",
            "Target creature phases out. Draw a card.", "Each opponent phases out.",
        ):
            with self.subTest(printed=printed):
                self.assertIsNone(_fixed_phase_out_template(
                    printed, card_name="Fixture", source_is_permanent=True,
                ))
        self.assertIsNone(_fixed_phase_out_template(
            "This creature phases out.", card_name="Fixture", source_is_permanent=False,
        ))

    def test_raw_phase_out_cannot_substitute_a_source_or_expand_target_authority(self):
        from types import SimpleNamespace

        from quorune.compiler.direct_target import direct_permanent_target_spec
        from quorune.phasing_model import PHASE_OUT_CAPABILITY, phase_out_effect
        from quorune.rules.occurrence_object_results import phase_out_runtime_dependencies

        source = phase_out_effect()
        target = phase_out_effect(targeted=True)
        schema = direct_permanent_target_spec("target creature").to_target_schema()
        closed = {"effects": (source,), "active_zone": "battlefield", "target_schema": None, "coverage": ()}
        required = phase_out_runtime_dependencies(SimpleNamespace(**closed))
        self.assertEqual({PHASE_OUT_CAPABILITY, PHASING_CAPABILITY}, set(required))
        self.assertIn("target.revalidate_resolution", phase_out_runtime_dependencies(
            SimpleNamespace(**{**closed, "effects": (target,), "target_schema": schema}),
        ))
        for invalid in (
            {"active_zone": "stack"},
            {"effects": ({**source, "card": "$target.0"},)},
            {"effects": ({**source, "schema_version": True},)},
            {"effects": ({**target, "card": "$target.1"},), "target_schema": schema},
            {"effects": (target,), "target_schema": {**schema, "count": 2}},
            {"effects": ({"op": "offer_optional_effect", "effects": [source]},)},
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "one complete"):
                phase_out_runtime_dependencies(SimpleNamespace(**{**closed, **invalid}))


from inherited_composition_fixture import InheritedNativeFixture


class FixedDirectPhaseOutNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "direct-phase-out.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "phasing-cards.json",
            "ability-kind-cost-modifier-cards.json", "activation-public-state-cards.json", "fixed-direct-phasing-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Direct phasing", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 24),
        ], ["Generic Bound Commander"])

    @staticmethod
    def leave_room_for_extra_draws(session):
        opening_hand = tuple(session.state.players["A"].zones["hand"])
        for object_id in opening_hand[:-3]:
            session.engine.move_card(object_id, "library", position="bottom", log=False)

    def test_phased_source_skips_beginning_of_step_and_phased_in_source_observes_again(self):
        for version in (1, 2):
            with self.subTest(version=version):
                session = self.session(61341712)
                if version == 1:
                    session.state.phasing_lifecycle_version = 1
                self.assertEqual(version, session.state.phasing_lifecycle_version)
                self.leave_room_for_extra_draws(session)
                font = self.add(session.engine, "Font of Mythos", seat="B")
                ripple = self.add(session.engine, "Reality Ripple", zone="hand")
                warp = self.add(session.engine, "Time Warp", zone="hand")
                for card in (font, ripple, warp):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"C": 4, "U": 3})
                self.checkpoint(session)
                self.cast_for(session, "A", ripple, [font.ref], reserve={"C": 3, "U": 2})
                self.resolve_top(session)
                self.assertTrue(session.state.cards[font.object_id].phased_out)
                self.cast_for(session, "A", warp, ["A"])
                self.resolve_top(session)
                extra, = session.state.extra_turns
                self.until(session, lambda game: game.state.current_turn is not None
                           and game.state.current_turn.turn_id == extra.turn_id
                           and game.state.step == "draw" and game.state.pending_decision.kind == "priority")
                self.assertTrue(session.state.cards[font.object_id].phased_out)
                self.assertEqual(int(version == 1), self.trigger_count(session, font))
                session = self.replay(session, load=True)
                self.assertEqual(version, session.state.phasing_lifecycle_version)
                self.until(session, lambda game: game.state.active_player == "B"
                           and game.state.step == "draw" and game.state.pending_decision.kind == "priority")
                self.assertFalse(session.state.cards[font.object_id].phased_out)
                self.assertEqual(1, self.trigger_count(session, font))
                self.replay(session, load=True)

    def test_announced_beginning_of_step_trigger_resolves_after_its_source_phases_out(self):
        session = self.session(61341713)
        self.leave_room_for_extra_draws(session)
        font = self.add(session.engine, "Font of Mythos", seat="B")
        ripple = self.add(session.engine, "Reality Ripple", zone="hand")
        for index in range(2):
            self.add(session.engine, "Island", ref=f"phase-response-mana-{index}")
        self.prepare(session)
        self.checkpoint(session)
        self.until(session, lambda game: game.state.active_player == "B"
                   and game.state.step == "draw" and game.state.pending_decision.kind == "priority")
        self.assertEqual(1, self.trigger_count(session, font))
        before = len(session.state.players["B"].zones["hand"])
        self.cast_for(session, "A", ripple, [font.ref])
        self.resolve_top(session)
        self.assertTrue(session.state.cards[font.object_id].phased_out)
        self.assertEqual(1, self.trigger_count(session, font))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before + 2, len(session.state.players["B"].zones["hand"]))
        self.replay(session, load=True)

    @staticmethod
    def activation_offer(session, seat, source):
        legal = session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in legal if row["id"].startswith("activate:" + source.ref + ":"))
        return offer

    def activate(self, session, seat, source, target=None, *, reserve=None):
        self.priority(session, seat)
        offer = self.activation_offer(session, seat, source)
        command = {"action_id": offer["id"], "pay": "auto"}
        if reserve is not None:
            command["reserve"] = reserve
        if target is not None:
            command["targets"] = [target.ref]
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("activated_ability", source.object_id),
                         (session.state.stack[-1].kind, session.state.stack[-1].source_object_id))

    def assert_phased(self, session, original, *, return_seat):
        current = session.state.cards[original.object_id]
        self.assertEqual((original.logical_object_id, original.zone_timestamp, "battlefield"),
                         (current.logical_object_id, current.zone_timestamp, current.zone))
        self.assertTrue(current.phased_out)
        self.assertEqual(PhasingRecord(return_seat), current.phasing_record)

    def test_three_paid_intrinsic_sources_phase_out_and_replay(self):
        session = self.session(702260101)
        names = ("Blink Dog", "Rainbow Efreet", "Teferi's Honor Guard")
        sources = tuple(self.add(session.engine, name) for name in names)
        for card in sources:
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 3, "W": 1, "U": 4})
        self.checkpoint(session)
        for source, reserve in zip(sources, ({"U": 4}, None, None), strict=True):
            self.activate(session, "A", source, reserve=reserve)
            self.assertFalse(session.state.cards[source.object_id].phased_out)
            self.assertIsNone(self.resolve(session))
            self.assert_phased(session, source, return_seat="A")
        for color in "CWU":
            self.assertEqual(0, session.state.players["A"].mana_pool.get(color, 0))
        for source in sources:
            legal = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            self.assertFalse(any(row["id"].startswith("activate:" + source.ref + ":") for row in legal))
        self.replay(session, load=True)

    def test_paid_targeted_abilities_keep_their_distinct_target_authority(self):
        session = self.session(702260102)
        engine = session.engine
        haystack = self.add(engine, "Haystack")
        illusionist = self.add(engine, "Vodalian Illusionist", seat="B")
        owned = self.add(engine, "Grizzly Bears", ref="phase-owned")
        rival = self.add(engine, "Grizzly Bears", seat="C", ref="phase-rival")
        illusionist.acquired_control_turn_count = -1
        for card in (haystack, illusionist, owned, rival):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 2})
        session.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(session)
        offer = self.activation_offer(session, "A", haystack)
        command = {"action_id": offer["id"], "targets": [rival.ref], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.activate(session, "A", haystack, owned)
        self.assertIsNone(self.resolve(session))
        self.assert_phased(session, owned, return_seat="A")
        self.assertTrue(session.state.cards[haystack.object_id].tapped)
        self.assertFalse(session.state.cards[rival.object_id].phased_out)
        self.activate(session, "B", illusionist, rival)
        self.assertIsNone(self.resolve(session))
        self.assert_phased(session, rival, return_seat="C")
        self.assertTrue(session.state.cards[illusionist.object_id].tapped)
        self.assertEqual(0, session.state.players["B"].mana_pool.get("U", 0))
        self.replay(session, load=True)

    def test_paid_ripple_removes_attacker_and_phases_foreign_equipment_indirectly(self):
        session = self.session(702260103)
        engine = session.engine
        attacker = self.add(engine, "Grizzly Bears")
        attacker.acquired_control_turn_count = -1
        equipment = self.add(engine, "Bonesplitter", seat="C")
        instant = self.add(engine, "Reality Ripple", seat="B", zone="hand")
        private = self.add(engine, "Shock", seat="D", zone="hand", ref="phase-private")
        attach_objects(session.state.cards, equipment, attacker, source_timestamp=equipment.zone_timestamp,
                       players=session.state.players)
        attacker.counters["+1/+1"] = 1
        for card in (attacker, equipment, instant):
            self.assert_closed(session, card)
        for ref in ("phase-island-first", "phase-island-second"):
            self.add(engine, "Island", seat="B", ref=ref)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(attacker, "B")])
        self.assertIn(attacker.object_id, session.state.combat.attackers)
        self.cast_for(session, "B", instant, [attacker.ref])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assert_phased(session, attacker, return_seat="A")
        self.assertNotIn(attacker.object_id, session.state.combat.attackers)
        phased_equipment = session.state.cards[equipment.object_id]
        self.assertTrue(phased_equipment.phased_out)
        self.assertTrue(phased_equipment.phasing_record.indirect)
        self.assertEqual(attacker.logical_object_id, phased_equipment.phasing_record.attached_object.logical_object_id)
        self.assertEqual(1, session.state.cards[attacker.object_id].counters["+1/+1"])
        self.assertEqual("graveyard", session.state.cards[instant.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.replay(session, load=True)

    def test_crystal_end_step_is_atomic_and_uses_the_original_source_incarnation(self):
        from quorune import phasing
        from quorune.errors import GameRuleError

        session = self.session(702260104)
        golem = self.add(session.engine, "Crystal Golem")
        blink = self.add(session.engine, "Cloudshift", zone="hand")
        self.assert_closed(session, golem)
        self.assert_closed(session, blink)
        self.add(session.engine, "Generic Bound Plains", ref="phase-blink-mana")
        self.prepare(session)
        self.checkpoint(session)
        self.until(session, lambda game: any(
            item.kind == "triggered_ability" and item.source_object_id == golem.object_id
            for item in game.state.stack
        ))
        original_trigger, = session.state.stack
        self.assertEqual("end_step", session.state.step)
        self.assertEqual(golem.logical_object_id, original_trigger.context["source_logical_object_id"])
        blink_branch = self.replay(session, load=True)
        real_commit = phasing._commit_phasing

        def fail_after_status(*args, **kwargs):
            affected = real_commit(*args, **kwargs)
            self.assertTrue(affected)
            raise GameRuleError("Injected phase-out status fault")

        with patch.object(phasing, "_commit_phasing", side_effect=fail_after_status):
            for _ in range(8):
                actor = session.pending_principals()[0]
                snapshot = authoritative_state_hash(session.state)
                result = session.act(actor, {"action_id": "pass"})
                if not result.ok:
                    self.assertIn("Injected phase-out status fault", result.summary)
                    break
            else:
                self.fail("End-step phase out did not reach its status transaction")
        self.assertEqual(snapshot, authoritative_state_hash(session.state))
        self.assertFalse(session.state.cards[golem.object_id].phased_out)
        session = self.replay(session, load=True)
        self.assertTrue(session.act(actor, {"action_id": "pass"}).ok)
        self.assert_phased(session, golem, return_seat="A")
        self.replay(session, load=True)

        initial = golem.logical_object_id
        self.cast_for(blink_branch, "A", blink, [golem.ref])
        self.resolve_top(blink_branch)
        returned = blink_branch.state.cards[golem.object_id]
        self.assertNotEqual(initial, returned.logical_object_id)
        self.assertEqual("battlefield", returned.zone)
        self.assertIsNone(self.resolve(blink_branch))
        self.assertFalse(blink_branch.state.cards[golem.object_id].phased_out)
        self.replay(blink_branch, load=True)


    def test_paid_haystack_loses_a_target_that_left_and_returned(self):
        session = self.session(702260105)
        engine = session.engine
        haystack = self.add(engine, "Haystack")
        target = self.add(engine, "Grizzly Bears")
        blink = self.add(engine, "Cloudshift", zone="hand")
        initial = target.logical_object_id
        self.prepare(session, mana={"C": 2, "W": 1})
        self.checkpoint(session)
        self.activate(session, "A", haystack, target, reserve={"W": 1})
        self.cast_for(session, "A", blink, [target.ref])
        self.resolve_top(session)
        current = session.state.cards[target.object_id]
        self.assertNotEqual(initial, current.logical_object_id)
        self.assertEqual("battlefield", current.zone)
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[target.object_id].phased_out)
        self.assertTrue(session.state.cards[haystack.object_id].tapped)
        self.assertFalse(any(event.code == "permanent.phase_out" for event in session.state.events))
        self.replay(session, load=True)

    def test_genuine_phase_out_owner_and_return_lifecycle_withdrawals(self):
        from copy import deepcopy

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.phasing_model import PHASE_OUT_CAPABILITY
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from scripts.update_capability_evidence import build_index, discover_tests

        profile = "commander_review"
        cards = [self.db.lookup(name, fuzzy=False)
                 for name in ("Rainbow Efreet", "Reality Ripple", "Crystal Golem", "Shock")]
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())

        def bindings(registry):
            semantics = SemanticRegistry()
            register_generated_programs(
                self.db, semantics, cards, trust_level="provisional", capability_registry=registry,
                capability_profile=profile, promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True,
            )
            result = []
            for card in cards:
                program = compile_best_available_card_program(
                    self.db, card, semantic_registry=semantics, capability_registry=registry,
                    capability_profile=profile,
                )
                result.append(bind_card_program_runtime(
                    program, capability_registry=registry, profile=profile,
                )["strict_capability_ready"])
            return result

        self.assertEqual([True, True, True, True], bindings(load_default_capability_registry()))
        for name in (PHASE_OUT_CAPABILITY, PHASING_CAPABILITY):
            with self.subTest(withdrawn=name):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == name).update(
                    status="blocked", blockers=["Test withdrawal of the actual owner"],
                )
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations,
                                    discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertEqual([False, False, False, True], bindings(registry))
