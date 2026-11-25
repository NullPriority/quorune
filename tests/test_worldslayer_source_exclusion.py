"""Native evidence for one attached damage result excluding its original Equipment."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler import trigger_result_templates
from quorune.compiler.destruction_templates import mass_destruction_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.qualified_destruction import (
    SOURCE_INCARNATION_EXCLUDING_SET, SOURCE_INCARNATION_EXCLUSION_FIELD,
    source_incarnation_excluding_destruction,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


WORLD = "Worldslayer"
MASS = "permanent.destroy.fixed_set"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class WorldslayerSourceExclusionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "announced-target-cardinality-cards.json": {"Darksteel Citadel"},
            "attached-destruction-set-cards.json": {WORLD, "Forest", "Island", "Sol Ring", "Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "combat-removal-cards.json": {"Planar Incision", "Isamaru, Hound of Konda"},
        }
        parts = []
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert names == {row["name"] for row in document["cards"]}, name
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "worldslayer-source-exclusion.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipment source exclusion", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(WORLD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, WORLD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _equip(self, game, source, creature):
        self.priority(game, "A")
        offered, = [
            row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith(f"activate:{source.ref}:")
        ]
        command = {"action_id": offered["id"], "targets": [creature.ref], "pay": "manual", "payment": {"C": 5}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(creature.object_id, game.state.cards[source.object_id].attached_to)

    def test_original_registration_keeps_normal_equip_and_the_exact_captured_source_scope(self):
        card, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = [p for p in programs if p.event == "damage.dealt"]
        self.assertLessEqual({MASS, "attachment.reference.current_or_lki", "trigger.event.normalized_damage"},
                            set(trigger.capability_dependencies))
        self.assertIsNone(trigger.target_schema)
        effect, = trigger.effects
        self.assertEqual(SOURCE_INCARNATION_EXCLUDING_SET, source_incarnation_excluding_destruction(effect))
        self.assertTrue(any("attachment.equip.fixed_mana" in p.capability_dependencies for p in programs))

    def test_exact_source_exclusion_payload_and_prior_ordinary_set_remain_distinct(self):
        _, programs = self._original()
        trigger = next(p for p in programs if p.event == "damage.dealt")
        effect = dict(trigger.effects[0])
        self.assertEqual("$source.zone_object", effect["source"])
        self.assertFalse(source_incarnation_excluding_destruction(dict(effect, source=None), resolved=True).exclude_source)
        source = source_incarnation_excluding_destruction(dict(effect, source="current"), resolved=True)
        self.assertTrue(source.exclude_source)
        for malformed in (
            dict(effect, source="$source"),
            dict(effect, **{SOURCE_INCARNATION_EXCLUSION_FIELD: True}),
            dict(effect, regeneration_prohibited=True),
            dict(effect, set=dict(effect["set"], exclude_source=False)),
            dict(effect, set=dict(effect["set"], controller_relation="actor")),
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                source_incarnation_excluding_destruction(malformed)
        legacy = mass_destruction_effect_template("Destroy all other permanents.").effects[0]
        self.assertNotIn(SOURCE_INCARNATION_EXCLUSION_FIELD, legacy)
        self.assertEqual("$source", legacy["source"])
        self.assertTrue(legacy["set"]["exclude_source"])

    def test_actual_equipped_combat_preserves_only_the_original_incarnation_and_indestructible(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                game = self.session(7552910 + blink)
                source = self.add(game.engine, WORLD, zone="hand")
                dealer = self.add(game.engine, "Grizzly Bears")
                dealer.acquired_control_turn_count = -1
                ordinary = self.add(game.engine, "Sol Ring", seat="C")
                forest = self.add(game.engine, "Forest", seat="B")
                citadel = self.add(game.engine, "Darksteel Citadel", seat="C")
                private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="slayer-private")
                response = None
                if blink:
                    response = self.add(game.engine, "Planar Incision", seat="B", zone="hand")
                    for index in range(2):
                        self.add(game.engine, "Island", seat="B", ref=f"slayer-blink-mana-{index}")
                self.prepare(game, mana={"C": 10})
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.assertIsNone(self.resolve(game))
                self._equip(game, source, dealer)
                self.attack(game, [(dealer, "B")])
                self.until(game, lambda current: self.trigger_count(current, source) == 1)
                self.assertEqual(38, game.state.players["B"].life)
                pending, = [item for item in game.state.stack if item.source_object_id == source.object_id]
                old_identity = pending.context["source_logical_object_id"]
                self.assertEqual(game.state.cards[source.object_id].logical_object_id, old_identity)
                if response is not None:
                    self.cast(game, "B", response, [source.ref])
                    self.resolve_top(game)
                    current = game.state.cards[source.object_id]
                    self.assertEqual("battlefield", current.zone)
                    self.assertNotEqual(old_identity, current.logical_object_id)
                    self.assertEqual(1, self.trigger_count(game, source))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                for permanent in (dealer, ordinary, forest):
                    self.assertEqual("graveyard", game.state.cards[permanent.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[citadel.object_id].zone)
                self.assertEqual("graveyard" if blink else "battlefield", game.state.cards[source.object_id].zone)
                self.assertFalse(game.state.cards[source.object_id].attached_to)
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                self.replay(game, load=True)

    def test_actual_combat_damage_from_an_unequipped_creature_does_not_trigger(self):
        game = self.session(7552920)
        source = self.add(game.engine, WORLD)
        equipped = self.add(game.engine, "Grizzly Bears", ref="not-attacking-equipment-recipient")
        other = self.add(game.engine, "Grizzly Bears", ref="actual-unequipped-dealer")
        other.acquired_control_turn_count = -1
        forest = self.add(game.engine, "Forest", seat="B")
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self._equip(game, source, equipped)
        self.attack(game, [(other, "B")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(38, game.state.players["B"].life)
        self.assertEqual("battlefield", game.state.cards[forest.object_id].zone)
        self.assertEqual(equipped.object_id, game.state.cards[source.object_id].attached_to)
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_omitting_exact_equipped_result_restores_one_material_residual(self):
        with patch.object(trigger_result_templates, "_equipment_source_excluding_destruction_result", return_value=None):
            source = self.db.lookup(WORLD, fuzzy=False)
            ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual("trigger_effect", residual.kind)
            report, _ = register_card(self.db, self.registry, WORLD)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_fixed_set_authority_withdrawal_denies_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        doc = deepcopy(raw)
        next(row for row in doc["capabilities"] if row["id"] == MASS).update(
            status="blocked", blockers=["Scoped simultaneous original Equipment exclusion withdrawal"])
        registry = CapabilityRegistry(doc)
        index = build_index(registry_value=doc, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((MASS,), profile=PROFILE)
        self.assertIn(f"status:{MASS}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, WORLD)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Sol Ring")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
