"""Prove the printed charge-counter modes and versioned Equipment attachment in native play."""

from copy import deepcopy
from dataclasses import replace
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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


JITTE = "Umezawa's Jitte"
PROFILE = "commander_review"


class EquippedModalStatsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "equipped-modal-stats.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "equipped-temporary-hexproof-cards.json",
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "equipped-modal-stats-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped modal stats", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_original_program_has_one_charge_payment_and_target_only_in_second_mode(self):
        card = self.db.lookup(JITTE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, JITTE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        modal, = (p for p in programs if p.event == "activate" and (p.target_schema or {}).get("modes"))
        trigger, = (p for p in programs if p.event == "damage.dealt")
        self.assertEqual(("charge", 2, "$source.zone_object"), tuple(
            trigger.effects[0][name] for name in ("counter", "amount", "card")
        ))
        self.assertIn("activation.source_counter_removal.fixed", modal.capability_dependencies)
        schema = modal.target_schema
        modes = tuple(schema["modes"].values())
        self.assertEqual((1, 1), (schema["min_modes"], schema["max_modes"]))
        self.assertEqual((0, 1, 0), tuple(len(mode["groups"]) for mode in modes))
        self.assertEqual({"op": "modify_stats_until_end_of_turn", "power": 2, "toughness": 2,
                          "card": {"kind": "source_attachment", "relation": "equipped_object",
                                   "required_card_type": "permanent", "schema_version": 2}}, modes[0]["effects"][0])
        self.assertIn(ATTACHMENT_INFORMATION_CAPABILITY, modal.capability_dependencies)
        weaker = deepcopy(schema)
        weaker["modes"]["mode_1"]["effects"][0]["card"]["schema_version"] = 1
        needed = tuple(value for value in modal.capability_dependencies if value != ATTACHMENT_INFORMATION_CAPABILITY)
        for weakened in (
            replace(modal, target_schema=weaker),
            replace(modal, capability_dependencies=needed,
                    capability_closure=self.registry.closure(needed, profile=PROFILE).to_dict()),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                weakened, capability_registry=self.registry, profile=PROFILE,
            )["strict"])

    def accept(self, game, command, seat="A"):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def actions(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [action for action in actions if action["id"].startswith(f"activate:{source.ref}:")]

    def modal(self, game, equipment):
        return next((action for action in self.actions(game, equipment)
                     if (action.get("target_schema") or {}).get("legal_modes")), None)

    def equip(self, game, equipment, creature):
        offered, = [action for action in self.actions(game, equipment)
                    if not (action.get("target_schema") or {}).get("legal_modes")]
        self.accept(game, {"action_id": offered["id"], "targets": [creature.ref], "pay": "auto"})
        self.resolve_top(game)

    def power_toughness(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, field) for field in ("power", "toughness"))

    def test_original_cast_equip_combat_and_each_paid_mode_preserve_authority_and_replay(self):
        game = self.session(98121601)
        gear = self.add(game.engine, JITTE, zone="hand")
        body = self.add(game.engine, "Grizzly Bears", ref="equipped-jitte-combat")
        blocker = self.add(game.engine, "Grizzly Bears", seat="C", ref="jitte-creature-target")
        public_land = self.add(game.engine, "Plains", seat="C", ref="jitte-invalid-target")
        body.acquired_control_turn_count = -1
        self.assert_closed(game, gear)
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", gear)
        self.resolve_top(game)
        self.assertIsNone(self.modal(game, gear))
        self.equip(game, gear, body)
        self.assertEqual(body.object_id, game.state.cards[gear.object_id].attached_to)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.attack(game, [(body, "B")])
        self.until(game, lambda current: self.trigger_count(current, gear) == 1)
        self.assertEqual(38, game.state.players["B"].life)
        self.resolve_top(game)
        self.assertEqual({"charge": 2}, game.state.cards[gear.object_id].counters)

        offered = self.modal(game, gear)
        self.assertEqual(["mode_1", "mode_2", "mode_3"], offered["target_schema"]["legal_modes"])
        normal = {"action_id": offered["id"], "pay": "auto"}
        shrink = {**normal, "modes": ["mode_2"], "targets": [blocker.ref]}
        for incorrect in (
            {**normal, "modes": [], "targets": []},
            {**normal, "modes": ["mode_1", "mode_3"], "targets": []},
            {**shrink, "targets": [public_land.ref]},
            {**shrink, "targets": []},
            {**normal, "modes": ["mode_3"], "targets": [blocker.ref]},
        ):
            self.reject(game, incorrect)
        self.reject(game, shrink, seat="D")
        self.accept(game, shrink)
        self.assertEqual({"charge": 1}, game.state.cards[gear.object_id].counters)
        self.assertNotIn(ATTACHMENT_INFORMATION_VERSION, game.state.stack[-1].context)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((1, 1), self.power_toughness(game, blocker))
        self.assertEqual((2, 2), self.power_toughness(game, body))
        life = game.state.players["A"].life
        offered = self.modal(game, gear)
        self.accept(game, {"action_id": offered["id"], "modes": ["mode_3"], "targets": [], "pay": "auto"})
        self.assertEqual(0, game.state.cards[gear.object_id].counters.get("charge", 0))
        self.assertEqual((), tuple(game.state.stack[-1].targets))
        self.assertNotIn(ATTACHMENT_INFORMATION_VERSION, game.state.stack[-1].context)
        self.resolve_top(game)
        self.assertEqual(life + 2, game.state.players["A"].life)
        self.assertIsNone(self.modal(game, gear))
        self.replay(game, load=True)

    def play_pump(self, seed, *, attached=True, destroy=False):
        game = self.session(seed)
        gear = self.add(game.engine, JITTE)
        first = self.add(game.engine, "Grizzly Bears", ref="jitte-announced-attached")
        next_body = self.add(game.engine, "Grizzly Bears", seat="B", ref="jitte-current-attached")
        theft = self.add(game.engine, "Magnetic Theft", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        for land in ("Mountain", "Plains", "Wastes"):
            self.add(game.engine, land, seat="B")
        for card in (gear, theft, removal):
            self.assert_closed(game, card)
        gear.counters["charge"] = 1
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        if attached:
            self.equip(game, gear, first)
        offered = self.modal(game, gear)
        self.accept(game, {"action_id": offered["id"], "modes": ["mode_1"], "targets": [], "pay": "auto"})
        self.assertEqual(0, game.state.cards[gear.object_id].counters.get("charge", 0))
        item = game.state.stack[-1]
        self.assertEqual([], item.targets)
        self.assertEqual(1, item.context[ATTACHMENT_INFORMATION_VERSION])
        initial = item.context["source_attachment_snapshot"]["attached_object"]
        self.assertEqual(first.object_id if attached else None, initial and initial["object_id"])
        if attached:
            self.priority(game, "B")
            self.accept(game, {"action_id": f"cast:{theft.ref}", "pay": "auto",
                              "targets": {"target_0": [gear.ref], "target_1": [next_body.ref]}}, seat="B")
            self.resolve_top(game)
            self.assertEqual(next_body.object_id, game.state.cards[gear.object_id].attached_to)
            if destroy:
                self.cast_for(game, "B", removal, [gear.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[gear.object_id].zone)
                retained = game.state.stack[-1].context["source_attachment_snapshot"]["attached_object"]
                self.assertEqual(next_body.object_id, retained["object_id"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((2, 2), self.power_toughness(game, first))
        self.assertEqual((4, 4) if attached else (2, 2), self.power_toughness(game, next_body))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertEqual((2, 2), self.power_toughness(game, next_body))
        self.replay(game, load=True)

    def test_pump_uses_live_attachment_or_final_departure_attachment_and_expires(self):
        for destroy in (False, True):
            with self.subTest(destroy=destroy):
                self.play_pump(98121610 + destroy, destroy=destroy)

    def test_unattached_pump_is_offered_pays_and_resolves_without_a_creature(self):
        self.play_pump(98121613, attached=False)

    def test_missing_selected_mode_capture_is_detected_at_native_activation(self):
        def only_legacy_top_level(program, _proposal):
            return program.effects if program is not None else ()

        with patch("quorune.rules.activation.commit._activation_attachment_effects", side_effect=only_legacy_top_level):
            with self.assertRaises(KeyError):
                self.play_pump(98121614, attached=False)
