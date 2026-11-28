"""Equipped-attacker modal trigger announcement, resolution, and replay."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record

from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.modal_program_closure import is_closed_fixed_modal_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_counter_reference import EVENT_COUNTER_CONTEXT, EVENT_COUNTER_OBJECT
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


ORACLE_ID = "1e2ee630-9d37-4396-ae18-697c1a3a4e62"
ORACLE_FIXTURE = "untargeted-untap-cards.json"
MODAL = "choice.modal.fixed_nonrepeating"
ATTACHMENT = "attachment.reference.current_or_lki"
COUNTER = "counter.producer.fixed_event_object"


class EquippedAttackModalCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()
        fixture = ROOT / "tests" / "fixtures" / ORACLE_FIXTURE
        with fixture.open(encoding="utf-8") as source:
            card = next(row for row in json.load(source)["cards"] if row["oracle_id"] == ORACLE_ID)
        cls.original = replace(
            query_record(card["oracle_text"]), name=card["name"], oracle_id=card["oracle_id"],
            type_line=card["type_line"], keywords=tuple(card["keywords"]),
            mana_cost=card["mana_cost"], power=None, toughness=None,
        )

    def compile(self, source=None):
        return compile_oracle_card(
            source or self.original,
            capability_registry=self.registry,
            capability_profile="commander_review",
        )

    def test_pinned_modal_trigger_spans_all_modes_and_preserves_equip(self):
        result = self.compile()
        self.assertEqual("exact", result.status, result.material_residuals)
        trigger, equip = result.faces[0].nodes
        printed_block, printed_equip = self.original.oracle_text.rsplit("\n", 1)
        self.assertEqual((printed_block, 0, len(printed_block)), (trigger.text, trigger.span.start, trigger.span.end))
        self.assertEqual((printed_equip, 5), (equip.text, equip.span.line))
        self.assertEqual(("attachment.equip.fixed_mana",), equip.capability_dependencies)
        self.assertEqual("creature.attacks", trigger.event)
        self.assertEqual((), trigger.effects)
        self.assertLessEqual({
            MODAL, ATTACHMENT, COUNTER, "trigger.event.normalized_public_action",
            "zone.draw.library_to_hand", "choice.affected_player.fixed_controller_discard",
            "permanent.untap.effect", "resolution.effect.fixed_homogeneous_target_set",
            "target.revalidate_resolution",
        }, set(trigger.capability_dependencies))
        modes = trigger.target_schema["modes"]
        self.assertEqual(("mode_1", "mode_2", "mode_3"), tuple(modes))
        self.assertEqual(["draw", "choose_cards_apnap"], [effect["op"] for effect in modes["mode_1"]["effects"]])
        self.assertEqual(EVENT_COUNTER_OBJECT, modes["mode_2"]["effects"][0]["card"])
        self.assertEqual([{
            "id": "mode_3_target_1", "zones": ["battlefield"],
            "categories": ["permanent"], "types_any": ["land"], "up_to": 2,
        }], modes["mode_3"]["groups"])

    def test_bound_branch_reuses_grammar_without_identity_and_unsupported_neighbors_stay_partial(self):
        renamed = replace(self.original, oracle_id="generic-equipment", name="Generic Inventory")
        self.assertEqual("exact", self.compile(renamed).status)
        for old, new in (
            ("equipped creature attacks", "equipped creature attacks alone"),
            ("equipped creature attacks", "a creature you control attacks"),
            ("that creature.", "that player."),
            ("target lands.", "target lands controlled by a player chosen at random."),
            ("choose one", "choose one at random"),
        ):
            with self.subTest(new=new):
                variant = replace(renamed, oracle_text=renamed.oracle_text.replace(old, new))
                self.assertNotEqual("exact", self.compile(variant).status)

    def test_captured_counter_branch_omission_is_detected(self):
        self.test_pinned_modal_trigger_spans_all_modes_and_preserves_equip()
        with patch("quorune.compiler.modal_context_nodes.entry_event_counter_result", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_pinned_modal_trigger_spans_all_modes_and_preserves_equip()


    def test_tap_target_binding_removes_only_revalidated_reference_slots(self):
        from types import SimpleNamespace

        from quorune.semantic_runtime.fixed_target_set_handlers import bind_revalidated_tap_target_cards

        item = SimpleNamespace(context={"targets_revalidated": True}, targets=[None, "live-land"])
        declared = {"op": "untap_targets", "cards": ["$target.0", "$target.1"], "maximum_targets": 2}
        resolved = {**declared, "cards": [None, "live-land"]}
        self.assertEqual(
            {**resolved, "cards": ["live-land"]},
            bind_revalidated_tap_target_cards(item, declared, resolved),
        )
        for forged in ([None, "$target.1"], ["$target.1", "$target.1"], ["$target.9", "$target.1"]):
            with self.subTest(forged=forged):
                self.assertEqual(
                    resolved,
                    bind_revalidated_tap_target_cards(item, {**declared, "cards": forged}, resolved),
                )
        item.context["targets_revalidated"] = False
        self.assertEqual(resolved, bind_revalidated_tap_target_cards(item, declared, resolved))


class EquippedAttackModalNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-attack-modal.sqlite3"
        fixture_names = (
            "bound-effect-program-cards.json", ORACLE_FIXTURE, "modal-event-composition-cards.json",
            "activation-restriction-material-cards.json",
        )
        build_fixture_database([ROOT / "tests" / "fixtures" / name for name in fixture_names], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Equipped attack mode witness",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def scene(self, seed, *, controller="A"):
        session = self.session(seed)
        equipment = self.add(
            session.engine, "Pip-Boy 3000", seat=controller,
            zone="hand" if controller == "A" else "battlefield",
        )
        attacker = self.add(session.engine, "Grizzly Bears", ref="equipped-declared-attacker")
        other = self.add(session.engine, "Grizzly Bears", ref="other-declared-attacker")
        for creature in (attacker, other):
            creature.acquired_control_turn_count = -1
        self.assert_closed(session, equipment)
        if controller != "A":
            attach_objects(
                session.state.cards, equipment, attacker,
                source_timestamp=equipment.zone_timestamp, players=session.state.players,
            )
        return session, equipment, attacker, other

    def prepare_attack(self, session, equipment, attacker, *, controller="A"):
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        if controller != "A":
            return
        self.cast_for(session, "A", equipment, reserve={"C": 2})
        self.settle(session)
        self.priority(session, "A")
        offered = next(
            action for action in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if action["id"].startswith("activate:" + equipment.ref + ":")
        )
        result = session.act("pilot:A", {"action_id": offered["id"], "targets": [attacker.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual(attacker.object_id, session.state.cards[equipment.object_id].attached_to)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("C", 0))

    def pending_mode(self, session, seat):
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        self.assertEqual(["pilot:" + seat], session.pending_principals())
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])

    def reject(self, session, principal, command):
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act(principal, command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def choose_mode(self, session, equipment, seat, mode, targets=()):
        self.pending_mode(session, seat)
        command = {"action_id": "choose", "modes": [mode], "targets": list(targets)}
        self.reject(session, "pilot:D", command)
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("priority", session.state.pending_decision.kind)
        self.assertEqual(1, self.trigger_count(session, equipment))
        item = next(item for item in session.state.stack if item.source_object_id == equipment.object_id)
        self.assertEqual((mode,), tuple(item.modes))
        self.assertEqual(tuple(targets), tuple(item.targets))
        return item

    def test_only_the_equipped_declaration_triggers(self):
        session, equipment, attacker, other = self.scene(6033000)
        self.prepare_attack(session, equipment, attacker)
        self.attack(session, [(other, "C")])
        self.assertNotEqual("semantic.target", session.state.pending_decision.kind)
        self.assertEqual(0, self.trigger_count(session, equipment))
        self.replay(session, load=True)

    def test_sort_inventory_uses_equipment_controller_and_private_draw_then_discard(self):
        session, equipment, attacker, other = self.scene(6033001, controller="B")
        private = self.add(session.engine, "Grizzly Bears", seat="B", zone="hand", ref="private-pip-inventory")
        self.prepare_attack(session, equipment, attacker, controller="B")
        self.attack(session, [(attacker, "C"), (other, "D")])
        self.pending_mode(session, "B")
        session = self.replay(session, load=True)
        before = {seat: set(session.state.players[seat].zones["hand"]) for seat in "AB"}
        self.choose_mode(session, equipment, "B", "mode_1")
        self.assertEqual("choice.apnap", self.resolve(session).kind)
        after = set(session.state.players["B"].zones["hand"])
        drawn, = after - before["B"]
        drawn_ref = session.state.cards[drawn].ref
        self.assertEqual(1, len(after) - len(before["B"]))
        self.assertIn(drawn_ref, json.dumps(session.packet("pilot:B", full=True)))
        for viewer in "ACD":
            packet = session.packet("pilot:" + viewer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(private.ref, json.dumps(packet))
            self.assertNotIn(drawn_ref, json.dumps(packet))
        command = {"action_id": "choose", "cards": [private.ref]}
        self.reject(session, "pilot:A", command)
        session = self.replay(session, load=True)
        decision = session.act("pilot:B", command)
        self.assertTrue(decision.ok, decision.summary)
        self.settle(session)
        self.assertEqual("graveyard", session.state.cards[private.object_id].zone)
        self.assertIn(drawn, session.state.players["B"].zones["hand"])
        self.assertEqual(before["A"], set(session.state.players["A"].zones["hand"]))
        self.assertEqual(len(before["B"]), len(session.state.players["B"].zones["hand"]))
        self.assertEqual({}, session.state.cards[attacker.object_id].counters)
        self.replay(session, load=True)

    def test_pick_a_perk_preserves_the_declared_object_after_reattachment_or_blink(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                session, equipment, attacker, other = self.scene(6033010 + blink)
                response = self.add(
                    session.engine, "Cloudshift" if blink else "Magnetic Theft",
                    seat="A" if blink else "B", zone="hand",
                )
                self.add(session.engine, "Generic Bound Plains" if blink else "Mountain", seat="A" if blink else "B")
                self.assert_closed(session, response)
                self.prepare_attack(session, equipment, attacker)
                self.attack(session, [(attacker, "C"), (other, "D")])
                identity = session.state.cards[attacker.object_id].logical_object_id
                item = self.choose_mode(session, equipment, "A", "mode_2")
                self.assertEqual({"reference": attacker.ref, "logical_object_id": identity}, item.context[EVENT_COUNTER_CONTEXT])
                if blink:
                    self.cast(session, "A", response, [attacker.ref])
                else:
                    self.priority(session, "B")
                    offered = self.action(session, "B", response)
                    result = session.act("pilot:B", {"action_id": offered["id"], "pay": "auto",
                        "targets": {"target_0": [equipment.ref], "target_1": [other.ref]}})
                    self.assertTrue(result.ok, result.summary)
                self.resolve_top(session)
                if blink:
                    self.assertNotEqual(identity, session.state.cards[attacker.object_id].logical_object_id)
                    self.assertIsNone(session.state.cards[equipment.object_id].attached_to)
                else:
                    self.assertEqual(other.object_id, session.state.cards[equipment.object_id].attached_to)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(0 if blink else 1, session.state.cards[attacker.object_id].counters.get("+1/+1", 0))
                self.assertEqual({}, session.state.cards[other.object_id].counters)
                self.assertEqual({}, session.state.cards[equipment.object_id].counters)
                self.replay(session, load=True)

    def test_check_map_commits_distinct_land_targets_before_priority_and_revalidates(self):
        for count, stale in ((0, False), (1, False), (2, False), (1, True), (2, True)):
            with self.subTest(count=count, stale=stale):
                session, equipment, attacker, other = self.scene(6033020 + count + 5 * stale)
                own = self.add(session.engine, "Forest", ref="own-pip-land")
                foreign = self.add(session.engine, "Island", seat="C", ref="foreign-pip-land")
                unselected = self.add(session.engine, "Mountain", seat="D", ref="untapped-unselected-pip-land")
                for land in (own, foreign):
                    land.tapped = True
                if stale:
                    response = self.add(session.engine, "Boomerang", seat="B", zone="hand")
                    for index in range(2):
                        self.add(session.engine, "Island", seat="B", ref=f"pip-response-mana-{index}")
                self.prepare_attack(session, equipment, attacker)
                self.attack(session, [(attacker, "B")])
                self.pending_mode(session, "A")
                for modes, targets in (
                    (["mode_2", "mode_3"], []), (["mode_1"], [own.ref]),
                    (["mode_3"], [attacker.ref]), (["mode_3"], [own.ref, own.ref]),
                    (["mode_3"], [own.ref, foreign.ref, unselected.ref]),
                ):
                    self.reject(session, "pilot:A", {"action_id": "choose", "modes": modes, "targets": targets})
                selected = [own.ref, foreign.ref][:count]
                item = self.choose_mode(session, equipment, "A", "mode_3", selected)
                hand = len(session.state.players["A"].zones["hand"])
                if stale:
                    self.cast(session, "B", response, [own.ref])
                    self.resolve_top(session)
                    self.assertEqual("hand", session.state.cards[own.object_id].zone)
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(count == 0, session.state.cards[own.object_id].tapped if not stale else False)
                self.assertEqual(count < 2, session.state.cards[foreign.object_id].tapped)
                self.assertFalse(session.state.cards[unselected.object_id].tapped)
                countered = any(
                    event.code == "stack.counter" and event.details.get("stack") == item.ref
                    for event in session.state.events
                )
                self.assertEqual(stale and count == 1, countered)
                self.assertEqual(hand + int(stale), len(session.state.players["A"].zones["hand"]))
                self.assertEqual({}, session.state.cards[other.object_id].counters)
                self.assertEqual({}, session.state.cards[attacker.object_id].counters)
                self.replay(session, load=True)

    def test_runtime_reconstructs_event_and_child_authority_from_the_modal_shape(self):
        session, equipment, _, _ = self.scene(6033040)
        program, = (
            program for program in session.engine.semantics.programs_for_oracle(equipment.oracle_id)
            if MODAL in program.capability_dependencies
        )
        self.assertTrue(is_closed_fixed_modal_program(program))
        for capability in (ATTACHMENT, COUNTER, "trigger.event.normalized_public_action", "permanent.untap.effect"):
            with self.subTest(capability=capability):
                remaining = tuple(cap for cap in program.capability_dependencies if cap != capability)
                changed = replace(
                    program, coverage=[], capability_dependencies=remaining,
                    capability_closure=self.registry.closure(remaining, profile="commander_review").to_dict(),
                )
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(bound["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + capability, bound["blockers"])
        for changed in (
            replace(program, event_condition=None),
            replace(program, event="creature.blocks"),
            replace(program, active_zone="hand"),
        ):
            self.assertFalse(is_closed_fixed_modal_program(changed))
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def test_disabling_mode_recipient_capture_is_detected_at_native_placement(self):
        def witness(seed):
            session, equipment, attacker, _ = self.scene(seed)
            self.prepare_attack(session, equipment, attacker)
            self.attack(session, [(attacker, "C")])
            item = self.choose_mode(session, equipment, "A", "mode_2")
            self.assertIn(EVENT_COUNTER_CONTEXT, item.context)

        witness(6033050)
        with patch("quorune.event_counter_reference.uses_modal_event_counter_recipient", return_value=False):
            with self.assertRaises(AssertionError):
                witness(6033051)
