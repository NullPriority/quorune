"""A native attached death returns the creature and the same Aura transformed."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.attachment_source_return import ATTACHMENT_DEATH_TRANSFORM_CAPABILITY
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


class AttachmentDeathTransformTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attachment-death-transform.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/attachment-death-transform-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original Aura and transformed return", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_complete_front_and_back_and_withdrawn_pair_authority(self):
        source = self.db.lookup("Journey to Eternity // Atzal, Cave of Eternity")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        code = compile_oracle_card(source, **options)
        self.assertEqual("exact", code.status, code.material_residuals)
        self.assertEqual(2, len(code.faces))
        node, = [ability for ability in code.faces[0].nodes if ability.event == "creature.dies"]
        self.assertIn(ATTACHMENT_DEATH_TRANSFORM_CAPABILITY, node.capability_dependencies)
        self.assertEqual([2, 7], [effect["schema_version"] for effect in node.effects])
        self.assertEqual(["$controller", True], [node.effects[0]["controller"], node.effects[1]["attachment_return"]["transformed"]])
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == ATTACHMENT_DEATH_TRANSFORM_CAPABILITY)
        row.update(status="blocked", blockers=["Test-only ordered attachment return withdrawal"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids={row["test_id"] for row in current["declarations"]})
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(source, **{**options, "capability_registry": registry}).status)

    def game(self, seed, *, sweeper=False, tokens=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        aura = self.add(game.engine, "Journey to Eternity // Atzal, Cave of Eternity", zone="hand", ref="journey-aura")
        answer = self.add(game.engine, "Planar Cleansing" if sweeper else "Murder", zone="hand", seat="B")
        rear_target = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="rear-face-grave-target")
        creature = None
        if tokens:
            raise_alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        else:
            creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="borrowed-enchanted-bears")
            game.engine.change_control(creature.object_id, "A", reason="Fixture borrows the original creature")
        if sweeper:
            for index in range(6):
                self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"sweeper-source-{index}")
        self.assert_closed(game, aura)
        self.assert_closed(game, answer)
        self.prepare(game, mana={"C": 2, "W": 1, "B": 1, "G": 1} if tokens else {"C": 4, "B": 2, "G": 2})
        if not sweeper:
            game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)
        if tokens:
            self.cast_for(game, "A", raise_alarm, reserve={"C": 1, "B": 1, "G": 1})
            self.resolve_top(game)
            creature = next(card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.cast(game, "A", aura, [creature.ref])
        self.resolve_top(game)
        live_aura = game.state.cards[aura.object_id]
        self.assertEqual(creature.object_id, live_aura.attached_to)
        if sweeper:
            self.until(game, lambda current: current.state.active_player == "B"
                       and current.state.phase == "precombat_main" and current.state.pending_decision.kind == "priority")
            self.cast(game, "B", answer)
        else:
            self.cast(game, "B", answer, [creature.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        trigger, = [item for item in game.state.stack if item.source_object_id == aura.object_id]
        self.assertEqual("A", trigger.controller)
        return game, aura, creature, rear_target

    def assert_atzal(self, game, aura):
        returned = game.state.cards[aura.object_id]
        self.assertEqual(("battlefield", "A", "Atzal, Cave of Eternity"),
                         (returned.zone, returned.controller, returned.active_face))
        self.assertIsNone(returned.attached_to)
        return returned

    def test_native_borrowed_creature_and_rear_face_activation_replay(self):
        game, aura, creature, rear_target = self.game(40072101)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        returned = game.state.cards[creature.object_id]
        self.assertEqual(("battlefield", "B", "A"), (returned.zone, returned.owner, returned.controller))
        atzal = self.assert_atzal(game, aura)
        self.priority(game, "A")
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        action, = [row for row in packet["legal"]["actions"] if row["id"].startswith(f"activate:{atzal.ref}:")
                   and (row.get("target_schema") or "return" in row.get("label", "").casefold())]
        accepted = game.act("pilot:A", {"action_id": action["id"], "targets": [rear_target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[rear_target.object_id].zone)
        self.replay(game, load=True)

    def test_simultaneous_departures_keep_both_originals(self):
        game, aura, creature, _ = self.game(40072102, sweeper=True)
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(("battlefield", "A"), (game.state.cards[creature.object_id].zone,
                                               game.state.cards[creature.object_id].controller))
        self.assert_atzal(game, aura)
        self.replay(game, load=True)

    def test_missing_token_return_still_returns_the_physical_aura(self):
        game, aura, token, _ = self.game(40072103, tokens=True)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        remembered = game.state.cards.get(token.object_id)
        self.assertFalse(remembered is not None and remembered.zone == "battlefield")
        self.assert_atzal(game, aura)
        self.replay(game, load=True)

    def test_omitting_the_second_owner_breaks_the_native_transform_witness(self):
        with patch("quorune.effect_runtime.zones_and_attachments.apply_attachment_source_return", return_value=None):
            game, aura, creature, _ = self.game(40072104)
            self.resolve_top(game)
            self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
            with self.assertRaises(AssertionError):
                self.assert_atzal(game, aura)
