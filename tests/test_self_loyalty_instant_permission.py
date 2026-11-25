"""Native evidence for an exact permanent's own instant-speed loyalty permission."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.carddb import CardDatabase
from quorune.compiler import action_permission_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.action_permissions import (
    ActionPermissionKind, SOURCE_LOYALTY_INSTANT_CAPABILITY, SOURCE_LOYALTY_INSTANT_HANDLERS,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
TEFERI = "Teferi, Master of Time"
EMPEROR = "The Wandering Emperor"
CREATURE = "Walking Corpse"
ACTION = SOURCE_LOYALTY_INSTANT_CAPABILITY
ORIGINALS = {
    TEFERI: ActionPermissionKind.ACTIVATE_SOURCE_LOYALTY_AT_INSTANT,
    EMPEROR: ActionPermissionKind.ACTIVATE_ENTERED_SOURCE_LOYALTY_AT_INSTANT,
}


class SelfLoyaltyInstantPermissionTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Shock", "Sol Ring"},
            "additional-continuous-carriers-cards.json": {CREATURE},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "self-loyalty-instant-permission-cards.json": set(ORIGINALS),
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        subsets = []
        for name, names in support.items():
            record = json.loads((FIXTURES / name).read_bytes())
            record["cards"] = [row for row in record["cards"] if row["name"] in names]
            assert names == {row["name"] for row in record["cards"]}, name
            ids = {row["oracle_id"] for row in record["cards"]}
            record["rulings"] = [row for row in record.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(record), encoding="utf-8")
            subsets.append(part)
        path = folder / "self-loyalty-instant-permission.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Self loyalty timing", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = tuple(semantics.programs_for_oracle(card.oracle_id))
        permission, = [program for program in programs if program.event == "action.permission"]
        activations = [program for program in programs if program.event == "activate" and program.effects]
        return card, permission, activations

    def test_exact_source_loyalty_descriptor_rejects_other_sources_and_added_permission_terms(self):
        handler = action_permission_templates.static_action_permission_handler
        for name, kind in ORIGINALS.items():
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                line, = [line for line in card.oracle_text.splitlines() if "loyalty abilities" in line]
                _, wire, capability = handler(line, source_name=name)
                self.assertEqual(ACTION, capability)
                self.assertEqual({"handler_id": SOURCE_LOYALTY_INSTANT_HANDLERS[kind],
                                  "schema_version": 1, "event": "action.permission",
                                  "permission": kind.value}, wire)
                self.assertIsNone(handler(line, source_name="Unrelated Source"))
                self.assertIsNone(handler(line + " You may activate another loyalty ability this turn.", source_name=name))
        self.assertIsNone(handler("You may activate loyalty abilities of planeswalkers you control any time you could cast an instant.",
                                  source_name=TEFERI))

    def test_complete_originals_keep_exact_timing_permission_and_every_loyalty_result(self):
        for name, kind in ORIGINALS.items():
            with self.subTest(name=name):
                card, permission, activations = self._original(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual("battlefield", permission.active_zone)
                self.assertIn(ACTION, permission.capability_dependencies)
                wire, = permission.handlers
                self.assertEqual(kind.value, wire["permission"])
                self.assertEqual(3, len(activations))
                unconditional = kind is ActionPermissionKind.ACTIVATE_SOURCE_LOYALTY_AT_INSTANT
                operation = "phase_out_permanent" if unconditional else "place_counters_on_targets"
                specific, = [program for program in activations if program.effects[0]["op"] == operation]
                schema = thaw_value(specific.target_schema)
                if unconditional:
                    self.assertEqual("opponent", schema["controller_relation"])
                    self.assertEqual(1, schema["count"])
                    self.assertEqual("phase_out_permanent", specific.effects[0]["op"])
                else:
                    self.assertEqual(1, schema["up_to"])
                    self.assertEqual(("place_counters_on_targets", "apply_source_characteristics_until_end_of_turn"),
                                     tuple(effect["op"] for effect in specific.effects))
                    self.assertEqual(["First Strike"], thaw_value(specific.effects[1]["keywords"]))

    def _actions(self, game, source):
        self.priority(game, "A")
        packet = game.packet("pilot:A", full=True)
        return [row for row in packet["decision"]["ctx"]["legal"]["actions"]
                if row["id"].startswith(f"activate:{source.ref}:")]

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        outcome = game.act(f"pilot:{actor}", command)
        self.assertFalse(outcome.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _loyalty(self, game, source):
        return game.state.cards[source.object_id].counters["loyalty"]

    def test_actual_teferi_uses_current_priority_on_the_next_players_turn_and_only_its_own_permission(self):
        game = self.session(77161010)
        source = self.add(game.engine, TEFERI, zone="hand")
        older = self.add(game.engine, EMPEROR)
        older.counters["loyalty"] = 3
        own = self.add(game.engine, CREATURE, ref="loyalty-own-creature")
        opposed = self.add(game.engine, CREATURE, seat="B", ref="loyalty-opposed-creature")
        self.prepare(game, mana={"C": 2, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, self._loyalty(game, source))
        first_turn = game.state.turn_sequence
        self.finish_turn(game)
        self.assertGreater(game.state.turn_sequence, first_turn)
        self.assertNotEqual("A", game.state.active_player)
        offered, = [row for row in self._actions(game, source) if row["id"].endswith(":ab3")]
        self.assertEqual([opposed.ref], offered["target_schema"]["legal_refs"])
        self.assertEqual([], self._actions(game, older))
        self._reject(game, "A", {"action_id": f"activate:{older.ref}:ab3", "targets": [own.ref]})
        self._reject(game, "A", {"action_id": offered["id"], "targets": [own.ref]})
        self._reject(game, "B", {"action_id": offered["id"], "targets": [opposed.ref]})
        self.assertNotIn(offered["id"], json.dumps(game.packet("pilot:D", full=True)))
        outcome = game.act("pilot:A", {"action_id": offered["id"], "targets": [opposed.ref]})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("loyalty", 0))
        self.assertEqual("activated_ability", game.state.stack[-1].kind)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[opposed.object_id].phased_out)
        self.assertFalse(game.state.cards[own.object_id].phased_out)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual([], self._actions(game, source))
        self.replay(game, load=True)

    def test_actual_emperor_reads_its_own_current_entry_on_a_nonempty_stack_and_expires(self):
        game = self.session(77161020)
        source = self.add(game.engine, EMPEROR, zone="hand")
        creature = self.add(game.engine, CREATURE)
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.prepare(game, active="B", actor="A", mana={"C": 2, "W": 2})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast(game, "B", shock, ["D"])
        below = game.state.stack[-1].ref
        self.cast(game, "A", source)
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(game.state.turn_sequence, current.entered_battlefield_turn_sequence)
        self.assertEqual([below], [item.ref for item in game.state.stack])
        self.assertEqual(3, self._loyalty(game, source))
        offered, = [row for row in self._actions(game, source) if row["id"].endswith(":ab3")]
        self.assertIn(creature.ref, offered["target_schema"]["legal_refs"])
        outcome = game.act("pilot:A", {"action_id": offered["id"], "targets": [creature.ref]})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertEqual(4, self._loyalty(game, source))
        self.assertEqual(("spell", "activated_ability"), tuple(item.kind for item in game.state.stack))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        subject = game.state.cards[creature.object_id]
        self.assertEqual(1, subject.counters["+1/+1"])
        self.assertIn("first strike", game.engine._combat_keywords(subject))
        self.assertEqual([], self._actions(game, source))
        self.assertEqual(40, game.state.players["D"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["D"].life)
        entry_turn = game.state.turn_sequence
        self.finish_turn(game)
        self.assertGreater(game.state.turn_sequence, entry_turn)
        self.assertNotEqual("A", game.state.active_player)
        self.assertEqual([], self._actions(game, source))
        self._reject(game, "A", {"action_id": offered["id"], "targets": [creature.ref]})
        self.assertNotIn("first strike", game.engine._combat_keywords(game.state.cards[creature.object_id]))
        self.assertEqual(1, game.state.cards[creature.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_emperor_can_pay_loyalty_and_select_zero_targets(self):
        game = self.session(77161030)
        source = self.add(game.engine, EMPEROR, zone="hand")
        creature = self.add(game.engine, CREATURE)
        self.prepare(game, mana={"C": 2, "W": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        offered, = [row for row in self._actions(game, source) if row["id"].endswith(":ab3")]
        self.assertIn(creature.ref, offered["target_schema"]["legal_refs"])
        result = game.act("pilot:A", {"action_id": offered["id"], "targets": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4, self._loyalty(game, source))
        self.assertIsNone(self.resolve(game))
        subject = game.state.cards[creature.object_id]
        self.assertEqual(0, subject.counters.get("+1/+1", 0))
        self.assertNotIn("first strike", game.engine._combat_keywords(subject))
        self.replay(game, load=True)

    def test_omitting_the_exact_static_permission_restores_the_printed_residuals(self):
        for name in ORIGINALS:
            card = self.db.lookup(name, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(any(node.event == "action.permission" for face in ir.faces for node in face.nodes))
        with patch.object(action_permission_templates, "_self_loyalty_instant_handler", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertFalse(any(node.event == "action.permission" for face in ir.faces for node in face.nodes))
                residual, = ir.material_residuals
                self.assertIn("loyalty abilities", residual.text)
                self.assertEqual("static/continuous text has no exact typed contract", residual.reason)

    def test_genuine_self_loyalty_timing_withdrawal_denies_both_complete_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == ACTION).update(
            status="blocked", blockers=["Scoped self loyalty timing permission authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((ACTION,), profile=PROFILE)
        self.assertIn(f"status:{ACTION}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Sol Ring")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
