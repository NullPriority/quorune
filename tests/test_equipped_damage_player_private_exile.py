"""The Equipment controller owns the private replayable permission."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_self_public_zone_casting as self_zone
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.opponent_library_exile_model import DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.private_opponent_top_exile import current_private_top_exile_permission
from scripts.build_test_database import build_fixture_database


CARD = "Dream-Thief's Bandana"
ORACLE_ID = "3c958b5f-680b-45fc-8c3d-27b23677436e"


class EquippedDamagePrivateExileTests(InheritedNativeFixture, unittest.TestCase):
    prepare = self_zone.SelfPublicZoneRuntimeTests.prepare

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "damage-private-exile.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/inherited-rule-composition-cards.json",
            ROOT / "tests/fixtures/attachment-damage-cards.json",
            ROOT / "tests/fixtures/equipped-damage-player-private-exile-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipment exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 20),
        ], ["Isamaru, Hound of Konda"])

    def test_original_and_nearby_role_or_payment_sentences(self):
        card = self.db.by_oracle_id(ORACLE_ID)

        def closure(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        ir = closure(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = (node for node in ir.faces[0].nodes if node.event == "damage.dealt")
        self.assertIn(DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual("$context.target", trigger.effects[0]["library_owner"])
        for old, new in (("a player", "an opponent"), ("combat damage", "damage"),
                         ("mana of any type", "mana of any color"), ("face down", "face up")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", closure(changed).status)
        with patch("quorune.compiler.opponent_library_exile_templates.each_opponent_library_top_exile_result",
                   return_value=None):
            self.assertNotEqual("exact", closure(card).status)

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == "attachment.reference.current_or_lki").update(
            status="blocked", blockers=["Withdraw the attached damage identity"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        blocked = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", blocked.status)

    def damage_fixture(self, *, seed, attacker, damaged, top_name):
        game = self.session(seed)
        host = game.engine
        source = self.add(host, CARD, ref="bandana-private-source")
        body = self.add(host, "Grizzly Bears", seat=attacker, ref="bandana-actual-attacker")
        body.acquired_control_turn_count = -1
        attach_objects(game.state.cards, source, body, source_timestamp=source.zone_timestamp, players=game.state.players)
        self.add(host, "Wastes", ref="bandana-colorless-one")
        self.add(host, "Wastes", ref="bandana-colorless-two")
        top = self.add(host, top_name, seat=damaged, zone="library", ref="bandana-only-known-exile")
        library = game.state.players[damaged].zones["library"]
        self.assertIn(top.object_id, library)
        library.remove(top.object_id)
        library.append(top.object_id)
        removal = self.add(host, "Disenchant", seat="C", zone="hand", ref="bandana-removal")
        self.add(host, "Generic Bound Plains", seat="C", ref="bandana-removal-white")
        self.add(host, "Wastes", seat="C", ref="bandana-removal-generic")
        self.assert_closed(game, source)
        self.assert_closed(game, top)
        self.prepare(game, actor=attacker, active=attacker)
        self.checkpoint(game)
        self.until(game, lambda s: s.state.pending_decision is not None and s.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:" + attacker, {"action_id": "attack", "attackers": {body.ref: damaged}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda s: s.state.cards[top.object_id].zone == "exile")
        return game, source, top, removal

    def assert_hidden_from(self, game, card, seat):
        state = game.packet("pilot:" + seat, full=True)["state"]
        row, = (entry for entry in state["players"][card.owner]["ex"] if entry["id"] == card.ref)
        self.assertEqual("?", row["n"])
        self.assertNotIn("cid", row)
        self.assertNotIn(card.ref, state.get("action_explanations", {}))
        self.assertIsNone(current_private_top_exile_permission(game.state, seat, card))

    def assert_only_equipment_controller_sees(self, game, top):
        card = game.state.cards[top.object_id]
        self.assertTrue(card.face_down)
        self.assertEqual(["A"], card.known_to)
        state = game.packet("pilot:A", full=True)["state"]
        row, = (entry for entry in state["players"][card.owner]["ex"] if entry["id"] == card.ref)
        self.assertEqual(card.printed_name, row["n"])
        for seat in "BCD":
            self.assert_hidden_from(game, card, seat)

    def test_actual_foreign_top_only_viewer_can_pay_any_type_after_equipment_leaves(self):
        game, source, top, removal = self.damage_fixture(
            seed=57624971, attacker="A", damaged="B", top_name="Grizzly Bears",
        )
        self.assert_only_equipment_controller_sees(game, top)
        game = self.replay(game, load=True)
        self.cast(game, "C", removal, [source.ref])
        self.resolve(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assert_only_equipment_controller_sees(game, top)
        self.until(game, lambda s: s.state.phase == "postcombat_main" and s.state.priority_player == "A")
        self.cast(game, "A", top)
        self.assertEqual("A", game.state.stack[-1].controller)
        self.assertTrue(self.resolve(game) is None)
        final = game.state.cards[top.object_id]
        self.assertEqual(("battlefield", "A", "B"), (final.zone, final.controller, final.owner))
        self.replay(game, load=True)

    def test_actual_other_players_attacker_can_damage_equipment_controller(self):
        game, _, top, _ = self.damage_fixture(seed=57624972, attacker="B", damaged="A", top_name="Forest")
        self.assert_only_equipment_controller_sees(game, top)
        retained = current_private_top_exile_permission(game.state, "A", game.state.cards[top.object_id])
        self.assertTrue(retained["allow_land"])
        self.assertTrue(retained["allow_any_mana_type"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "land:" + top.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.replay(game, load=True)

    def test_actual_departed_viewer_leaves_foreign_card_hidden(self):
        game, _, top, _ = self.damage_fixture(seed=57624973, attacker="A", damaged="B", top_name="Forest")
        self.assert_only_equipment_controller_sees(game, top)
        self.until(game, lambda s: s.state.pending_decision is not None
                   and s.state.pending_decision.kind == "priority" and s.state.priority_player == "A")
        result = game.act("pilot:A", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(result.ok, result.summary)
        current = game.state.cards[top.object_id]
        self.assertEqual("exile", current.zone)
        self.assertTrue(current.face_down)
        for seat in "BCD":
            self.assert_hidden_from(game, current, seat)
        self.assertIsNone(current_private_top_exile_permission(game.state, "A", current))
        self.replay(game, load=True)
