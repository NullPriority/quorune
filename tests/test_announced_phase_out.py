"""Ordinary cast and replay proof for phasing up to X selected creatures."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
import test_announced_target_cardinality as controls
import test_phasing as phasing
import test_splice as splice_tests
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.compiler.announced_target_sets import announced_target_set_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.phasing_model import PHASE_OUT_TARGET_SET_CAPABILITY, announced_phase_out_effect, validate_phase_out_effect
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


SOURCE = "Veil Current"
BLUE = "Echo Ledger"
PROFILE = "commander_review"


class AnnouncedPhaseOutRuntimeTests(unittest.TestCase):
    witness = controls.AnnouncedTargetCardinalityRuntimeTests
    register_fixture_cards_on_add = True
    session = witness.session
    add = witness.add
    prepare = witness.prepare
    checkpoint = witness.checkpoint
    resolve = witness.resolve
    replay = witness.replay
    binding = witness.binding
    actions = witness.actions
    action = witness.action
    cast = witness.cast
    _stage_end_step = phasing.PhasingRuntimeTests._stage_end_step
    _pass_end_step = phasing.PhasingRuntimeTests._pass_end_step
    start_splice = splice_tests.SpliceRuntimeTests.start_splice
    add_splice = splice_tests.SpliceRuntimeTests.add_splice
    splice_actions = splice_tests.SpliceRuntimeTests.splice_actions

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "announced-phasing.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json",
            "announced-phase-out-cards.json",
            "phasing-cards.json",
            "ability-kind-cost-modifier-cards.json",
            "announced-target-cardinality-cards.json",
            "targeted-spell-cost-cards.json",
            "fixed-stack-spell-copy-cards.json",
            "splice-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced phasing", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_paid_creature_set_and_indirect_attachment_phase_out_then_return_and_replay(self):
        session = self.session(702_267)
        engine = session.engine
        source = self.add(engine, SOURCE, zone="hand")
        resource = self.add(engine, BLUE, zone="hand", ref="actor-private-credit")
        foreign = self.add(engine, BLUE, seat="D", zone="hand", ref="opposing-private-credit")
        one = self.add(engine, "Generic Bound Body", seat="B", ref="phase-first")
        two = self.add(engine, "Generic Bound Body", seat="B", ref="phase-second")
        third = self.add(engine, "Generic Bound Body", seat="C", ref="phase-unselected")
        equipment = self.add(engine, "Bonesplitter", seat="D", ref="phase-indirect")
        attach_objects(session.state.cards, equipment, one,
                       source_timestamp=equipment.zone_timestamp, players=session.state.players)
        one.counters["+1/+1"] = 1
        original = {card.object_id: (card.logical_object_id, card.zone_timestamp)
                    for card in (one, two, equipment)}
        session.state.players["A"].mana_pool["U"] = 1
        self._stage_end_step(session, "A")
        self.assertTrue(self.binding(session, source)["strict_capability_ready"])
        action = self.action(session, source)
        option, = action["cost_options"]
        self.assertEqual(2, option["choice_schema"]["x"]["maximum"])
        self.assertEqual("x", option["target_schema"]["groups"][0]["maximum_from"])
        self.assertEqual({one.ref, two.ref, third.ref}, set(option["target_schema"]["legal_refs"]))
        for viewer in "BCD":
            self.assertNotIn(resource.ref, json.dumps(session.packet("pilot:" + viewer, full=True)))
        self.assertNotIn(foreign.ref, json.dumps(session.packet("pilot:A", full=True)))

        draft = {"action_id": action["id"], "x": 2, "exile_cards": [resource.ref],
                 "pay": "manual", "payment": {"U": 1}}
        before = authoritative_state_hash(session.state)
        for refs in ([one.ref, one.ref], [one.ref, two.ref, third.ref], [equipment.ref]):
            denied = session.act("pilot:A", {**draft, "targets": refs})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {**draft, "targets": [one.ref, two.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, session.state.stack[-1].x_value)
        self.assertEqual(2, session.state.stack[-1].context["target_schema_override"]["up_to"])
        session = self.replay(session, load=True)
        start = len(session.state.events)
        self.assertIsNone(self.resolve(session))
        events = [event for event in session.state.events[start:] if event.code == "permanent.phase_out"]
        self.assertEqual(1, len(events))
        self.assertEqual({one.ref, two.ref, equipment.ref}, set(events[0].details["out"]))
        self.assertTrue(session.state.cards[equipment.object_id].phasing_record.indirect)
        self.assertFalse(session.state.cards[third.object_id].phased_out)
        self.assertEqual("exile", session.state.cards[resource.object_id].zone)
        self.assertEqual("hand", session.state.cards[foreign.object_id].zone)
        self._pass_end_step(session, "B")
        for object_id, signature in original.items():
            current = session.state.cards[object_id]
            self.assertEqual(signature, (current.logical_object_id, current.zone_timestamp))
            self.assertFalse(current.phased_out)
            self.assertIsNone(current.phasing_record)
        self.assertEqual(one.object_id, session.state.cards[equipment.object_id].attached_to)
        self.assertEqual({"+1/+1": 1}, session.state.cards[one.object_id].counters)
        self.replay(session, load=True)

    def test_zero_selection_and_partial_illegality_use_only_current_legal_targets(self):
        for select in (False, True):
            with self.subTest(select=select):
                session = self.session(702_270 + int(select))
                source = self.add(session.engine, SOURCE, zone="hand")
                removed = self.add(session.engine, "Generic Bound Body", seat="B", ref="phase-removed")
                remaining = self.add(session.engine, "Generic Bound Body", seat="D", ref="phase-remaining")
                kill = self.add(session.engine, "Murder", seat="C", zone="hand")
                session.state.players["C"].mana_pool.update({"B": 2, "C": 1})
                self.prepare(session, mana={"U": 1, "C": 2})
                self.checkpoint(session)
                action = self.action(session, source)
                answer = {"action_id": action["id"], "targets": [removed.ref, remaining.ref] if select else [],
                          "x": 2, "pay": "auto"}
                result = session.act("pilot:A", answer)
                self.assertTrue(result.ok, result.summary)
                if select:
                    self.cast(session, "C", kill, [removed.ref])
                session = self.replay(session, load=True)
                start = len(session.state.events)
                self.assertIsNone(self.resolve(session))
                effects = [event.details["out"] for event in session.state.events[start:]
                           if event.code == "permanent.phase_out"]
                self.assertEqual([[remaining.ref]] if select else [], effects)
                self.assertEqual("graveyard" if select else "battlefield", session.state.cards[removed.object_id].zone)
                self.replay(session, load=True)

    def test_copy_retains_announced_target_limit_and_phases_only_its_selected_set(self):
        session = self.session(702_273)
        source = self.add(session.engine, SOURCE, zone="hand")
        copy_spell = self.add(session.engine, "Reverberate", zone="hand")
        one, two, three = (self.add(session.engine, "Generic Bound Body", seat=seat,
                                    ref="phase-copy-" + seat) for seat in "BCD")
        self.prepare(session, mana={"U": 1, "C": 2, "R": 2})
        self.checkpoint(session)
        send = session.act("pilot:A", {"action_id": self.action(session, source)["id"],
            "x": 2, "targets": [one.ref, two.ref], "pay": "manual", "payment": {"U": 1, "C": 2}})
        self.assertTrue(send.ok, send.summary)
        original = session.state.stack[-1]
        self.cast(session, "A", copy_spell, [original.ref])
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "targets": [one.ref, two.ref, three.ref]})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        shorter = session.act("pilot:A", {"action_id": "choose", "targets": [one.ref]})
        self.assertFalse(shorter.ok, shorter.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        accepted = session.act("pilot:A", {"action_id": "choose", "targets": [two.ref, three.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        copy, = [item for item in session.state.stack if item.kind == "spell_copy"]
        self.assertEqual((2, 2), (copy.x_value, copy.context["target_schema_override"]["up_to"]))
        self.assertIsNone(self.resolve(session))
        batches = [set(event.details["out"]) for event in session.state.events
                   if event.code == "permanent.phase_out"]
        self.assertEqual([{two.ref, three.ref}, {one.ref}], batches)
        self.replay(session, load=True)

    def test_splice_offers_its_payable_x_and_retains_the_declared_target_count(self):
        session = self.session(702_274)
        source = self.add(session.engine, SOURCE, zone="hand")
        dream = self.add(session.engine, "Everdream", zone="hand")
        one = self.add(session.engine, "Generic Bound Body", seat="B", ref="phase-splice-b")
        two = self.add(session.engine, "Generic Bound Body", seat="C", ref="phase-splice-c")
        taxed = self.add(session.engine, "Boreal Elemental", seat="D", ref="phase-splice-taxed")
        self.prepare(session, mana={"U": 2, "C": 4})
        self.checkpoint(session)
        self.start_splice(session, source)
        self.add_splice(session, dream)
        option, = self.splice_actions(session)["cast"]["cost_options"]
        self.assertEqual(4, option["choice_schema"]["x"]["maximum"])
        self.assertEqual("x", option["target_schema"]["groups"][0]["maximum_from"])
        self.assertEqual({one.ref, two.ref, taxed.ref}, set(option["target_schema"]["legal_refs"]))
        library = len(session.state.players["A"].zones["library"])
        result = session.act("pilot:A", {"action_id": "cast", "x": 2,
                                          "targets": [one.ref, two.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.stack[-1].x_value)
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(library - 1, len(session.state.players["A"].zones["library"]))
        self.assertTrue(all(session.state.cards[o.object_id].phased_out for o in (one, two)))
        self.replay(session, load=True)

    def test_announced_phasing_shape_and_paired_authority_withdrawal(self):
        card = self.db.lookup(SOURCE)
        result = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", result.status, result.material_residuals)
        primary, = (node for face in result.faces for node in face.nodes if node.event == "resolve")
        self.assertIn(PHASE_OUT_TARGET_SET_CAPABILITY, primary.capability_dependencies)
        effect = announced_phase_out_effect()
        self.assertEqual("target_set", validate_phase_out_effect(effect))
        for bad in ({**effect, "cards": "$target.0"}, {**effect, "schema_version": True},
                    {**effect, "maximum_targets": 2}, {**effect, "announced_target_count": 1}):
            with self.assertRaises(ValueError):
                validate_phase_out_effect(bad)
        with self.assertRaises(ValueError):
            validate_phase_out_effect({**effect, "cards": ["one", "two"], "maximum_targets": 1}, runtime=True)
        for outside in ("Up to X target permanents phase out.", "Exactly X target creatures phase out.",
                        "Up to X target creatures phase out. Draw a card."):
            self.assertIsNone(announced_target_set_effect_template(outside))

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == PHASE_OUT_TARGET_SET_CAPABILITY)
        row.update(status="blocked", blockers=["Announced phasing authority withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        withdrawn = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", withdrawn.status)
        with patch("quorune.compiler.announced_target_sets.announced_target_set_effect_template", return_value=None):
            omitted = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", omitted.status)
