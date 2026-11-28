"""Sword of the Paruns uses live attachment status and an untargeted choice."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachment_references import ATTACHMENT_INFORMATION_CAPABILITY, ATTACHMENT_INFORMATION_VERSION
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.tap_state_capability_shapes import OPTIONAL_EQUIPPED_TAP_STATE_FIELD
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record


NAME = "Sword of the Paruns"
PROFILE = "commander_review"
TEMPLATE = "choose-tap-or-untap-equipped-creature-v1"
TEXT = "{3}: You may tap or untap equipped creature."
FIXTURE = "sword-of-the-paruns-cards.json"
SUPPORT_FIXTURES = (
    "commander-cast-quantity-cards.json",
    "attached-cast-restriction-cards.json",
    "cast-counter-and-source-depletion-threshold-cards.json",
)


class OptionalEquippedTapStateCompilerTests(unittest.TestCase):
    def test_exact_untargeted_equipment_span_and_nearby_fail_closed_grammar(self):
        record = replace(query_record(TEXT), type_line="Artifact — Equipment")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((TEMPLATE, TEXT, len(TEXT)), (node.template_id, node.text, node.span.end))
        self.assertIsNone(node.target_schema)
        self.assertIn(ATTACHMENT_INFORMATION_CAPABILITY, node.capability_dependencies)
        self.assertIn("permanent.tap_state.optional_choice", node.capability_dependencies)
        self.assertNotIn("target.revalidate_resolution", node.capability_dependencies)
        for neighbor in (
            replace(record, type_line="Artifact"),
            replace(record, oracle_text=TEXT.replace("equipped creature", "enchanted creature")),
            replace(record, oracle_text=TEXT.replace("You may", "Another player may")),
        ):
            with self.subTest(text=neighbor.oracle_text, type_line=neighbor.type_line):
                self.assertTrue(compile_oracle_card(neighbor, capability_registry=registry).material_residuals)
        with patch("quorune.compiler.tap_state_templates.OptionalEquippedCreatureTapStateTemplate.compiled",
                   return_value=(None, (), None, ())):
            self.assertTrue(compile_oracle_card(record, capability_registry=registry).material_residuals)


class OptionalEquippedTapStateNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixtures = ROOT / "tests/fixtures"
        database = Path(cls.temporary.name) / "optional-equipped-tap-state.sqlite3"
        build_fixture_database([fixtures / p for p in (FIXTURE, *SUPPORT_FIXTURES)], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Optional Equipment tap state", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)
        return result

    def reject(self, game, seat, command):
        initial = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(initial, authoritative_state_hash(game.state))

    def ability(self, game, gear, *, equip=False, seat="A"):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((action for action in actions if action["id"].startswith(f"activate:{gear.ref}:")
                     and bool(action.get("target_schema")) == equip), None)

    def equip_gear(self, game, gear, body, *, foreign=None):
        offered = self.ability(game, gear, equip=True)
        self.assertIsNotNone(offered)
        self.assertIn(body.ref, offered["target_schema"]["legal_refs"])
        if foreign is not None:
            self.assertNotIn(foreign.ref, offered["target_schema"]["legal_refs"])
            self.reject(game, "A", {"action_id": offered["id"], "targets": [foreign.ref], "pay": "auto"})
        self.accept(game, "A", {"action_id": offered["id"], "targets": [body.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual(body.object_id, game.state.cards[gear.object_id].attached_to)

    def activate_gear(self, game, gear, *, check_target=None):
        offered = self.ability(game, gear)
        self.assertIsNotNone(offered)
        command = {"action_id": offered["id"], "pay": "auto"}
        if check_target is not None:
            self.reject(game, "B", command)
            self.reject(game, "A", {**command, "targets": [check_target.ref]})
        prior = game.state.players["A"].mana_pool["C"]
        self.accept(game, "A", command)
        self.assertEqual(prior - 3, game.state.players["A"].mana_pool.get("C", 0))
        item = game.state.stack[-1]
        self.assertEqual([], item.targets)
        self.assertEqual("A", item.controller)
        self.assertEqual(1, item.context[ATTACHMENT_INFORMATION_VERSION])

    def choose(self, game, available, selected, *, check_privacy=False):
        self.assertIsNotNone(self.resolve(game))
        decision = game.state.pending_decision
        self.assertEqual([available, "decline"],
                         decision.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]["legal_values"])
        if check_privacy:
            for seat in "BCD":
                packet = game.packet("pilot:" + seat, full=True)
                self.assertNotIn('"legal_values": ["' + available + '", "decline"]', json.dumps(packet))
                self.reject(game, seat, {"action_id": "choose", "choice": available})
            impossible = "untap" if available == "tap" else "tap"
            self.reject(game, "A", {"action_id": "choose", "choice": impossible})
            game = self.replay(game, load=True)
        self.accept(game, "A", {"action_id": "choose", "choice": selected})
        self.assertIsNone(self.resolve(game))
        return game

    @staticmethod
    def stats(game, creature):
        return tuple(game.engine._numeric_stat(creature.object_id, field) for field in ("power", "toughness"))

    def test_actual_sword_pays_equips_and_updates_each_live_anthem_and_lethal_damage(self):
        game = self.session(70126011)
        gear = self.add(game.engine, NAME, zone="hand")
        body = self.add(game.engine, "Grizzly Bears", ref="sword-wearer")
        tapped = self.add(game.engine, "Grizzly Bears", ref="sword-tapped-ally")
        idle = self.add(game.engine, "Grizzly Bears", ref="sword-untapped-ally")
        marked = self.add(game.engine, "Grizzly Bears", ref="sword-marked-ally")
        rival = self.add(game.engine, "Grizzly Bears", seat="B", ref="sword-tapped-rival")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="sword-untapped-rival")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        tapped.tapped = rival.tapped = True
        self.assert_closed(game, gear)
        self.assert_closed(game, bolt)
        self.prepare(game, mana={"C": 19})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", gear)
        self.resolve_top(game)
        self.assertEqual((2, 2), self.stats(game, idle))
        gear = game.state.cards[gear.object_id]
        self.activate_gear(game, gear)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((2, 2), self.stats(game, idle))
        self.equip_gear(game, gear, body, foreign=rival)
        self.assertEqual((2, 4), self.stats(game, body))
        self.assertEqual((2, 4), self.stats(game, idle))
        for creature in (tapped, rival, other):
            self.assertEqual((2, 2), self.stats(game, creature))
        self.cast_for(game, "B", bolt, [marked.ref])
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[marked.object_id].zone)
        self.assertEqual((2, 4), self.stats(game, marked))
        self.activate_gear(game, gear, check_target=other)
        game = self.choose(game, "tap", "decline", check_privacy=True)
        self.assertEqual((2, 4), self.stats(game, body))
        self.activate_gear(game, gear)
        game = self.choose(game, "tap", "tap")
        self.assertEqual((4, 2), self.stats(game, body))
        self.assertEqual((4, 2), self.stats(game, tapped))
        for creature in (idle, rival, other):
            self.assertEqual((2, 2), self.stats(game, creature))
        self.assertEqual("graveyard", game.state.cards[marked.object_id].zone)
        self.activate_gear(game, gear)
        game = self.choose(game, "untap", "untap")
        self.assertEqual((2, 4), self.stats(game, body))
        self.assertEqual((2, 4), self.stats(game, idle))
        self.assertEqual((2, 2), self.stats(game, tapped))
        self.replay(game, load=True)

    def test_actual_sword_rechecks_reattachment_control_and_departure(self):
        for destroy in (False, True):
            with self.subTest(destroy=destroy):
                self.exercise_reattachment(destroy)

    def exercise_reattachment(self, destroy):
        game = self.session(70126020 + destroy)
        gear = self.add(game.engine, NAME)
        first = self.add(game.engine, "Grizzly Bears", ref="first-sword-wearer")
        latest = self.add(game.engine, "Grizzly Bears", seat="B", ref="new-sword-wearer")
        ally = self.add(game.engine, "Grizzly Bears", seat="B", ref="second-sword-controller-ally")
        first_ally = self.add(game.engine, "Grizzly Bears", ref="first-sword-controller-ally")
        steal_creature = self.add(game.engine, "Control Magic", seat="B", zone="hand")
        theft = self.add(game.engine, "Magnetic Theft", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        steal = self.add(game.engine, "Steal Artifact", seat="B", zone="hand")
        self.add(game.engine, "Vedalken Orrery", seat="B")
        for card in (gear, steal_creature, theft, removal, steal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6})
        game.state.players["B"].mana_pool.update({"R": 1, "U": 4, "C": 5, "W": 1})
        self.checkpoint(game)
        self.equip_gear(game, gear, first)
        self.assertEqual((2, 4), self.stats(game, first))
        self.activate_gear(game, gear)
        self.cast_for(game, "B", steal_creature, [first.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[first.object_id].controller)
        self.assertEqual("A", game.state.cards[gear.object_id].controller)
        self.assertEqual(first.object_id, game.state.cards[gear.object_id].attached_to)
        self.assertEqual((2, 2), self.stats(game, first))
        self.assertEqual((2, 4), self.stats(game, first_ally))
        self.priority(game, "B")
        self.accept(game, "B", {"action_id": f"cast:{theft.ref}", "pay": "auto",
                                "targets": {"target_0": [gear.ref], "target_1": [latest.ref]}})
        self.resolve_top(game)
        self.assertEqual(latest.object_id, game.state.cards[gear.object_id].attached_to)
        self.assertEqual((2, 4), self.stats(game, first_ally))
        self.assertEqual((2, 2), self.stats(game, first))
        self.assertEqual((2, 2), self.stats(game, latest))
        if destroy:
            self.cast_for(game, "B", removal, [gear.ref])
            self.resolve_top(game)
            retained = game.state.stack[-1].context["source_attachment_snapshot"]["attached_object"]
            self.assertEqual(latest.object_id, retained["object_id"])
        else:
            self.cast_for(game, "B", steal, [gear.ref])
            self.resolve_top(game)
            sword = game.state.cards[gear.object_id]
            self.assertEqual("B", sword.controller)
            self.assertEqual(latest.object_id, sword.attached_to)
            self.assertEqual((2, 4), self.stats(game, ally))
        self.assertEqual((2, 2), self.stats(game, first_ally))
        self.assertEqual((2, 2) if destroy else (2, 4), self.stats(game, first))
        self.assertEqual("A", game.state.stack[-1].controller)
        game = self.replay(game, load=True)
        game = self.choose(game, "tap", "tap", check_privacy=True)
        self.assertTrue(game.state.cards[latest.object_id].tapped)
        self.assertFalse(game.state.cards[first.object_id].tapped)
        self.assertEqual((2, 2), self.stats(game, first))
        self.assertEqual((2, 2) if destroy else (4, 2), self.stats(game, latest))
        self.assertEqual((2, 2), self.stats(game, ally))
        self.replay(game, load=True)

    def test_actual_sword_rejects_a_returned_creature_and_preserves_v3(self):
        game = self.session(70126031)
        gear = self.add(game.engine, NAME)
        body = self.add(game.engine, "Grizzly Bears", ref="sword-original-body")
        flicker = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        for card in (flicker, removal, gear):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6})
        game.state.players["B"].mana_pool.update({"C": 2, "W": 2})
        self.checkpoint(game)
        self.equip_gear(game, gear, body)
        self.activate_gear(game, gear)
        old = game.state.cards[body.object_id].logical_object_id
        self.cast_for(game, "B", removal, [gear.ref])
        self.resolve_top(game)
        self.cast_for(game, "B", flicker, [body.ref])
        self.resolve_top(game)
        new_body = game.state.cards[body.object_id]
        self.assertNotEqual(old, new_body.logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(new_body.tapped)
        self.replay(game, load=True)

    def test_marked_runtime_choice_rejects_wrong_scope_and_missing_attachment_authority(self):
        game = self.session(70126040)
        gear = self.add(game.engine, NAME)
        program, = (row for row in game.engine.semantics.programs_for_oracle(gear.oracle_id)
                    if row.event == "activate" and row.effects and row.effects[0].get(OPTIONAL_EQUIPPED_TAP_STATE_FIELD))
        effect = deepcopy(program.effects[0])
        effect["then_by_choice"]["untap"][0]["card"]["schema_version"] = 1
        required = tuple(dependency for dependency in program.capability_dependencies
                         if dependency != ATTACHMENT_INFORMATION_CAPABILITY)
        for altered in (
            replace(program, effects=(effect,)),
            replace(program, target_schema={"categories": ["permanent"], "count": 1}),
            replace(program, capability_dependencies=required,
                    capability_closure=self.registry.closure(required, profile=PROFILE).to_dict()),
        ):
            with self.subTest(altered=altered):
                report = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(report["strict"], report)
