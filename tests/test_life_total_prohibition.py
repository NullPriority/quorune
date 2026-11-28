from __future__ import annotations

"""The controller's settled life total uses the same authority as payment and damage."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.life_gain_permission import ALLOW_ALL_LIFE_GAIN
from quorune.life_state import LifeStateError, available_life_for_payment, prepare_life_payment
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.life_prohibitions import collect_life_gain_permissions
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Platinum Emperion"


class LifeTotalProhibitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current controller life total", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "life-total-prohibition.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "life-total-prohibition-cards.json",
            fixtures / "life-gain-prohibition-cards.json",
            fixtures / "player-life-exchange-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "current-life-effect-amount-cards.json",
            fixtures / "life-total-setting-cards.json",
            fixtures / "color-cast-land-entry-cards.json",
        ], database)
        cls.use_database(database)

    def submit(self, game, seat, command, *, valid=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def actions(self, game, seat):
        self.priority(game, seat)
        return {row["id"]: row for row in game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]}

    def test_actual_cast_keeps_damage_and_draw_but_blocks_life_until_source_departure(self):
        game = self.session(119_800_101)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL, zone="hand")
        erebos = self.add(game.engine, "Erebos, God of the Dead")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        draw = self.add(game.engine, "Revitalize", zone="hand")
        self.add(game.engine, "Plains", ref="life-lock-white-a")
        self.add(game.engine, "Swamp", ref="life-lock-black-a")
        self.add(game.engine, "Plains", seat="B", ref="life-lock-white-b")
        self.add(game.engine, "Mountain", seat="B", ref="life-lock-red-b")
        for card in (source, erebos, bolt, destroy, draw):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 11})
        game.state.players["B"].mana_pool["C"] = 1
        self.checkpoint(game)

        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        permission = collect_life_gain_permissions(game.engine)
        self.assertFalse(permission.allows_gain("A"))
        self.assertFalse(permission.allows_loss("A"))
        self.assertTrue(permission.allows_loss("B"))
        self.assertEqual(0, available_life_for_payment(game.engine, "A"))
        zero = prepare_life_payment(game.engine, "A", 0)
        self.assertEqual(0, zero.amount)
        with self.assertRaises(LifeStateError):
            prepare_life_payment(game.engine, "A", 1)

        before = game.state.players["A"].life
        self.cast(game, "B", bolt, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, game.state.players["A"].life)
        self.assertTrue(any(event.code == "effect.damage" and event.details.get("target") == "A"
                            and event.details.get("amount") == 3 for event in game.state.events))

        prior_hand = set(game.state.players["A"].zones["hand"])
        self.cast(game, "A", draw)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, game.state.players["A"].life)
        gained, = set(game.state.players["A"].zones["hand"]) - prior_hand
        gained_ref = game.state.cards[gained].ref
        for seat in "BCD":
            self.assertNotIn(gained_ref, json.dumps(game.packet("pilot:" + seat, full=True)))

        ability, = (ability for ability in game.engine._activated_abilities(
                    game.state.cards[erebos.object_id]) if ability.life_payment > 0)
        command = {"action_id": f"activate:{erebos.ref}:{ability.ability_id}", "pay": "auto"}
        self.assertNotIn(command["action_id"], self.actions(game, "A"))
        self.submit(game, "A", command, valid=False)

        self.cast(game, "B", destroy, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertIn(command["action_id"], self.actions(game, "A"))
        self.submit(game, "A", command)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before - 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_phyrexian_payment_loses_only_the_life_alternative(self):
        for black_available in (False, True):
            with self.subTest(black_available=black_available):
                game = self.session(119_800_102 + black_available)
                game.state.config.semantic_policy = "trusted_only"
                source = self.add(game.engine, ORIGINAL)
                spell = self.add(game.engine, "Vault Skirge", zone="hand")
                self.assert_closed(game, source)
                self.assert_closed(game, spell)
                mana = {"C": 1, **({"B": 1} if black_available else {})}
                self.prepare(game, mana=mana)
                self.checkpoint(game)
                command = {"action_id": "cast:" + spell.ref, "pay": "auto"}
                self.assertEqual(black_available, command["action_id"] in self.actions(game, "A"))
                before = game.state.players["A"].life
                self.submit(game, "A", command, valid=black_available)
                if black_available:
                    self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if black_available else "hand", game.state.cards[spell.object_id].zone)
                self.assertEqual(before, game.state.players["A"].life)
                self.replay(game, load=True)

    def test_native_exchange_keeps_both_totals_when_either_half_is_blocked(self):
        for initial in (17, 31):
            with self.subTest(initial=initial):
                game = self.session(119_800_110 + initial)
                game.state.config.semantic_policy = "trusted_only"
                source = self.add(game.engine, ORIGINAL)
                conduit = self.add(game.engine, "Soul Conduit")
                self.assert_closed(game, source)
                self.assert_closed(game, conduit)
                game.state.players["A"].life = initial
                game.state.players["B"].life = 24
                self.prepare(game, mana={"C": 6})
                self.checkpoint(game)
                offered = next(key for key in self.actions(game, "A") if key.startswith(f"activate:{conduit.ref}:"))
                self.submit(game, "A", {"action_id": offered, "targets": ["A", "B"], "pay": "auto"})
                self.assertIsNone(self.resolve(game))
                self.assertEqual((initial, 24), (game.state.players["A"].life, game.state.players["B"].life))
                self.assertTrue(game.state.cards[conduit.object_id].tapped)
                self.replay(game, load=True)

    def test_lifelink_and_actual_commander_damage_survive_the_locked_recipient(self):
        game = self.session(119_800_113)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL)
        collar = self.add(game.engine, "Basilisk Collar", seat="B")
        self.assert_closed(game, source)
        self.assert_closed(game, collar)
        commander_id = game.state.players["B"].zones["command"][0]
        commander = game.engine.move_card(commander_id, "battlefield", controller="B", log=False)
        commander.acquired_control_turn_count = -1
        self.prepare(game, actor="B", active="B", mana={"C": 2})
        self.checkpoint(game)
        offered = next(key for key in self.actions(game, "B") if key.startswith(f"activate:{collar.ref}:"))
        self.submit(game, "B", {"action_id": offered, "targets": [commander.ref], "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        before = {seat: game.state.players[seat].life for seat in "AB"}
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        self.submit(game, "B", {"action_id": "attack", "attackers": {commander.ref: "A"}})
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(before["A"], game.state.players["A"].life)
        self.assertEqual(before["B"] + 2, game.state.players["B"].life)
        self.assertEqual(2, sum(game.state.players["A"].commander_damage_received.values()))
        self.replay(game, load=True)

    def test_control_change_retargets_the_live_permission(self):
        game = self.session(119_800_114)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL)
        control = self.add(game.engine, "Control Magic", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, control)
        self.prepare(game, actor="B", active="B", mana={"C": 2, "U": 2})
        self.checkpoint(game)
        before = collect_life_gain_permissions(game.engine)
        self.assertFalse(before.allows_loss("A"))
        self.assertTrue(before.allows_loss("B"))
        self.cast(game, "B", control, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        game = self.replay(game, load=True)
        after = collect_life_gain_permissions(game.engine)
        self.assertTrue(after.allows_loss("A"))
        self.assertFalse(after.allows_loss("B"))
        self.assertGreater(available_life_for_payment(game.engine, "A"), 0)
        self.assertEqual(0, available_life_for_payment(game.engine, "B"))

    def test_global_life_setting_moves_other_totals_and_preserves_the_current_controller(self):
        game = self.session(119_800_115)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL)
        spell = self.add(game.engine, "Repay in Kind", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, spell)
        game.state.players["B"].life = 14
        self.prepare(game, mana={"C": 5, "B": 2})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 14, 14, 14), tuple(game.state.players[seat].life for seat in "ABCD"))
        self.replay(game, load=True)

    def test_ordinary_land_play_omits_the_forbidden_life_alternative(self):
        game = self.session(119_800_116)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL)
        land = self.add(game.engine, "Hallowed Fountain", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, land)
        self.prepare(game)
        self.checkpoint(game)
        action = self.actions(game, "A")["play-land:" + land.ref]
        self.assertNotIn("pay_life", action.get("choice_schema", {}))
        command = {"action_id": action["id"], "pay_life": True}
        self.submit(game, "A", command, valid=False)
        self.submit(game, "A", {"action_id": action["id"]})
        arrived = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", True, 40), (arrived.zone, arrived.tapped, game.state.players["A"].life))
        self.replay(game, load=True)

    def test_current_authority_and_exact_scope_are_required(self):
        from quorune.life_gain_permission import LIFE_TOTAL_PROHIBITION_CAPABILITY
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = self.db.lookup(ORIGINAL)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == LIFE_TOTAL_PROHIBITION_CAPABILITY).update(
            status="blocked", blockers=["The scoped life total prohibition has been withdrawn"])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(
            source, capability_registry=registry, capability_profile="commander_review").status)
        self.assertNotEqual("exact", compile_oracle_card(
            replace(source, oracle_text="Your opponents' life totals can't change."),
            capability_registry=self.registry, capability_profile="commander_review").status)

    def test_removing_the_damage_permission_is_detected_by_the_native_witness(self):
        witness = self.test_actual_cast_keeps_damage_and_draw_but_blocks_life_until_source_departure
        with patch("quorune.damage.collect_life_gain_permissions", return_value=ALLOW_ALL_LIFE_GAIN):
            with self.assertRaisesRegex(AssertionError, "40 != 37"):
                witness()
