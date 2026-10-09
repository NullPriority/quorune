from __future__ import annotations

import ast
from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import jsonschema

from common import DB_PATH
from quorune.carddb import CardDatabase, CardRecord
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    CapabilityRegistryError,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.component_resolution import implementation_component_resolves
from quorune.semantics import SemanticProgram


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = (
    ROOT
    / "quorune"
    / "rules"
    / "capability-registry.json"
)


def _registry_value() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _test_ids() -> set[str]:
    result: set[str] = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        result.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
    return result


def _lightning_bolt_record() -> CardRecord:
    """Return a compiler fixture independent of the selected card database."""

    return CardRecord(
        oracle_id="00000000-0000-4000-8000-00000000b017",
        name="Lightning Bolt",
        mana_cost="{R}",
        mana_value=1.0,
        type_line="Instant",
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("R",),
        color_identity=("R",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="1993-08-05",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class ComponentResolutionTests(unittest.TestCase):
    def test_identical_source_reuses_exports_without_importing_the_module(self):
        source = (
            "raise RuntimeError('component validation must not execute source')\n"
            "class ComponentResolutionWitness: pass\n"
            "def regular(): pass\n"
            "async def asynchronous(): pass\n"
            "assigned = 1\nannotated: int = 2\n"
        )
        component = "quorune.rules.component_resolution."
        with patch.object(Path, "read_text", return_value=source), patch(
            "quorune.rules.component_resolution.ast.parse", wraps=ast.parse
        ) as parse:
            for name in (
                "ComponentResolutionWitness", "regular", "asynchronous",
                "assigned", "annotated",
            ):
                self.assertTrue(implementation_component_resolves(component + name))
            self.assertFalse(implementation_component_resolves(component + "missing"))
            self.assertEqual(1, parse.call_count)

    def test_same_size_same_timestamp_edits_and_deletion_cannot_reuse_trust(self):
        with TemporaryDirectory() as directory:
            package = Path(directory) / "quorune"
            rules = package / "rules"
            rules.mkdir(parents=True)
            implementation = rules / "witness.py"
            component = "quorune.rules.witness.Export"
            implementation.write_text("class Export: pass\n", encoding="utf-8")
            original = implementation.stat()
            timestamp = original.st_mtime_ns
            resolver_path = rules / "component_resolution.py"
            with patch("quorune.rules.component_resolution.__file__", str(resolver_path)):
                self.assertTrue(implementation_component_resolves(component))
                implementation.write_text("class Absent: pass\n", encoding="utf-8")
                os.utime(implementation, ns=(timestamp, timestamp))
                changed = implementation.stat()
                self.assertEqual(original.st_size, changed.st_size)
                self.assertEqual(timestamp, changed.st_mtime_ns)
                self.assertFalse(implementation_component_resolves(component))
                implementation.write_text("class Export: pass\n", encoding="utf-8")
                self.assertTrue(implementation_component_resolves(component))
                implementation.unlink()
                self.assertFalse(implementation_component_resolves(component))

    def test_malformed_and_unreadable_replacements_fail_closed_then_recover(self):
        component = "quorune.rules.component_resolution.RepairedWitness"
        with patch.object(Path, "read_text", return_value="class RepairedWitness: pass\n"):
            self.assertTrue(implementation_component_resolves(component))
        for replacement in ("class RepairedWitness(:\n", "\x00"):
            with self.subTest(replacement=replacement), patch.object(
                Path, "read_text", return_value=replacement
            ):
                self.assertFalse(implementation_component_resolves(component))
        for failure in (OSError("unreadable"), UnicodeError("invalid encoding")):
            with self.subTest(failure=failure), patch.object(Path, "read_text", side_effect=failure):
                self.assertFalse(implementation_component_resolves(component))
        with patch.object(Path, "read_text", return_value="class RepairedWitness: pass\n"):
            self.assertTrue(implementation_component_resolves(component))

    def test_package_exports_and_missing_names_keep_existing_resolution_boundaries(self):
        with TemporaryDirectory() as directory:
            package = Path(directory) / "quorune"
            rules = package / "rules"
            rules.mkdir(parents=True)
            child = package / "witness"
            child.mkdir()
            initializer = child / "__init__.py"
            initializer.write_text(
                "from elsewhere import Imported\n"
                "class Export: pass\n"
                "def outer():\n    class Nested: pass\n"
                "left, right = (1, 2)\n",
                encoding="utf-8",
            )
            with patch("quorune.rules.component_resolution.__file__", str(rules / "component_resolution.py")):
                self.assertTrue(implementation_component_resolves("quorune.witness"))
                self.assertTrue(implementation_component_resolves("quorune.witness.Export"))
                for name in ("Imported", "Nested", "left", "missing"):
                    self.assertFalse(implementation_component_resolves("quorune.witness." + name))
                self.assertFalse(implementation_component_resolves("other.witness.Export"))
                self.assertFalse(implementation_component_resolves("quorune.absent.Export"))


class CapabilityRegistryTests(unittest.TestCase):
    def test_registry_matches_schema_rules_snapshot_and_test_evidence(self):
        value = _registry_value()
        schema = json.loads(
            (
                ROOT
                / "schemas"
                / "rule-capability-registry.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(value)
        rule_index = json.loads(
            (ROOT / "rules" / "rule-index.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            rule_index["effective_date"], value["effective_date"]
        )
        self.assertEqual(
            rule_index["source_sha256"], value["source_sha256"]
        )
        known_rules = {row["rule_id"] for row in rule_index["rules"]}
        known_tests = _test_ids()
        for capability in value["capabilities"]:
            with self.subTest(capability["id"]):
                self.assertFalse(
                    set(capability["official_rules"]) - known_rules
                )
                evidence = {
                    test_id
                    for field in (
                        "positive_tests",
                        "negative_tests",
                        "interaction_tests",
                        "multiplayer_tests",
                        "privacy_tests",
                        "replay_tests",
                    )
                    for test_id in capability[field]
                }
                self.assertFalse(evidence - known_tests)

    def test_broad_damage_aggregate_stays_blocked(self):
        registry = load_default_capability_registry()
        closure = registry.aggregate_closure(
            "cr-120-damage", profile="commander_duel"
        )
        self.assertFalse(closure.trusted)
        self.assertTrue(
            any(
                "damage.replacement.order" in blocker
                for blocker in closure.blockers
            )
        )
        self.assertTrue(
            any(
                "damage.trigger.noncombat" in blocker
                for blocker in closure.blockers
            )
        )

    def test_bounded_combat_capabilities_are_not_broad_mechanic_aggregates(self):
        registry = load_default_capability_registry()
        for mechanic_id in ("trample", "first-strike", "double-strike"):
            with self.subTest(mechanic_id):
                with self.assertRaisesRegex(
                    CapabilityRegistryError,
                    "Unknown mechanic aggregate",
                ):
                    registry.aggregate_closure(
                        mechanic_id,
                        profile="commander_duel",
                    )

    def test_untap_all_dependency_fails_closed(self):
        value = _registry_value()
        single = next(
            row
            for row in value["capabilities"]
            if row["id"] == "permanent.untap.effect"
        )
        aggregate = next(
            row
            for row in value["capabilities"]
            if row["id"] == "permanent.untap.all_creatures"
        )
        aggregate["status"] = "trusted"
        aggregate["blockers"] = []
        single["status"] = "blocked"
        single["blockers"] = ["dependency mutation"]

        closure = CapabilityRegistry(value).closure(
            ["permanent.untap.all_creatures"],
            profile="commander_review",
        )

        self.assertFalse(closure.trusted)
        self.assertTrue(
            any(
                "status:permanent.untap.effect:blocked" in blocker
                for blocker in closure.blockers
            )
        )

    def test_flying_dependency_fails_closed_when_reach_is_blocked(self):
        registry = load_default_capability_registry()
        trusted = registry.closure(
            ["combat.block.flying"],
            profile="commander_review",
        )
        self.assertTrue(trusted.trusted)
        self.assertIn("combat.block.reach", trusted.reachable)

        value = _registry_value()
        reach = next(
            row
            for row in value["capabilities"]
            if row["id"] == "combat.block.reach"
        )
        reach["status"] = "blocked"
        reach["blockers"] = ["dependency mutation"]
        closure = CapabilityRegistry(value).closure(
            ["combat.block.flying"],
            profile="commander_review",
        )

        self.assertFalse(closure.trusted)
        self.assertTrue(
            any(
                "status:combat.block.reach:blocked" in blocker
                for blocker in closure.blockers
            )
        )

    def test_hexproof_dependency_fails_closed_without_target_revalidation(self):
        registry = load_default_capability_registry()
        trusted = registry.closure(
            ["target.protection.hexproof_permanent"],
            profile="commander_review",
        )
        self.assertTrue(trusted.trusted)
        self.assertIn("target.revalidate_resolution", trusted.reachable)

        value = _registry_value()
        revalidation = next(
            row
            for row in value["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        revalidation["status"] = "blocked"
        revalidation["blockers"] = ["dependency mutation"]
        closure = CapabilityRegistry(value).closure(
            ["target.protection.hexproof_permanent"],
            profile="commander_review",
        )

        self.assertFalse(closure.trusted)
        self.assertTrue(
            any(
                "status:target.revalidate_resolution:blocked" in blocker
                for blocker in closure.blockers
            )
        )

    def test_shroud_dependency_fails_closed_without_target_revalidation(self):
        registry = load_default_capability_registry()
        trusted = registry.closure(
            ["target.protection.shroud_permanent"],
            profile="commander_review",
        )
        self.assertTrue(trusted.trusted)
        self.assertIn("target.revalidate_resolution", trusted.reachable)

        value = _registry_value()
        revalidation = next(
            row
            for row in value["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        revalidation["status"] = "blocked"
        revalidation["blockers"] = ["dependency mutation"]
        closure = CapabilityRegistry(value).closure(
            ["target.protection.shroud_permanent"],
            profile="commander_review",
        )

        self.assertFalse(closure.trusted)
        self.assertTrue(
            any(
                "status:target.revalidate_resolution:blocked" in blocker
                for blocker in closure.blockers
            )
        )

    def test_draw_subcapabilities_fail_closed_without_base(self):
        registry = load_default_capability_registry()
        for capability_id in (
            "zone.draw.reveal_as_drawn",
            "zone.draw.result_generated_ordering",
            "zone.draw.specifically_drawn_card_actions",
        ):
            with self.subTest(capability_id=capability_id):
                trusted = registry.closure(
                    [capability_id], profile="commander_review"
                )
                self.assertTrue(trusted.trusted)
                self.assertIn(
                    "zone.draw.library_to_hand", trusted.reachable
                )

        value = _registry_value()
        base = next(
            row
            for row in value["capabilities"]
            if row["id"] == "zone.draw.library_to_hand"
        )
        base["status"] = "blocked"
        base["blockers"] = ["dependency mutation"]
        mutated = CapabilityRegistry(value)
        for capability_id in (
            "zone.draw.reveal_as_drawn",
            "zone.draw.result_generated_ordering",
            "zone.draw.specifically_drawn_card_actions",
        ):
            with self.subTest(mutated=capability_id):
                closure = mutated.closure(
                    [capability_id], profile="commander_review"
                )
                self.assertFalse(closure.trusted)
                self.assertTrue(
                    any(
                        "status:zone.draw.library_to_hand:blocked"
                        in blocker
                        for blocker in closure.blockers
                    )
                )

    def test_canonical_damage_assignment_fails_closed_without_strike_steps(
        self,
    ):
        value = _registry_value()
        strike_steps = next(
            row
            for row in value["capabilities"]
            if row["id"] == "combat.damage.participation.strike_steps"
        )
        strike_steps["status"] = "blocked"
        strike_steps["blockers"] = ["dependency mutation"]

        closure = CapabilityRegistry(value).closure(
            ["combat.damage.assignment.canonical"],
            profile="commander_review",
        )

        self.assertFalse(closure.trusted)
        self.assertTrue(
            any(
                "status:combat.damage.participation.strike_steps:blocked"
                in blocker
                for blocker in closure.blockers
            )
        )

    def test_closure_is_transitive_deterministic_and_profile_scoped(self):
        registry = load_default_capability_registry()
        first = registry.closure(
            [
                "damage.result.multitype_permanent",
                "damage.result.player_life",
                "target.public.player_or_damageable_permanent",
            ],
            profile="commander_review",
        )
        second = registry.closure(
            reversed(first.requested), profile="commander_review"
        )
        self.assertTrue(first.trusted)
        self.assertEqual(first, second)
        self.assertIn("damage.amount.positive", first.reachable)
        self.assertIn("target.revalidate_resolution", first.reachable)
        self.assertNotIn("damage.result.infect", first.reachable)
        self.assertNotIn("damage.result.wither", first.reachable)
        self.assertNotIn("damage.replacement.order", first.reachable)
        with self.assertRaisesRegex(
            CapabilityRegistryError, "Unknown capability profile"
        ):
            registry.closure(first.requested, profile="vintage-chaos")

    def test_registry_rejects_cycles_and_incomplete_trust_evidence(self):
        value = _registry_value()
        target_revalidation = next(
            row
            for row in value["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        target_revalidation["dependencies"] = [
            "target.public.player_or_damageable_permanent"
        ]
        target_revalidation["dependency_fail_closed_status"] = "passed"
        target_revalidation["dependency_fail_closed_rationale"] = ""
        with self.assertRaisesRegex(
            CapabilityRegistryError, "dependency cycle"
        ):
            CapabilityRegistry(value)

        value = _registry_value()
        target_revalidation = next(
            row
            for row in value["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        target_revalidation["negative_tests"] = []
        with self.assertRaisesRegex(
            CapabilityRegistryError, "requires negative_tests"
        ):
            CapabilityRegistry(value)

    def test_registry_rejects_schema_drift_and_type_coercion(self):
        value = _registry_value()
        value["future_trust_switch"] = True
        with self.assertRaisesRegex(
            CapabilityRegistryError, "unknown fields"
        ):
            CapabilityRegistry(value)

        value = _registry_value()
        value["capabilities"][0]["version"] = "1"
        with self.assertRaisesRegex(
            CapabilityRegistryError, "version must be positive"
        ):
            CapabilityRegistry(value)

    def test_unreviewed_node_shape_has_no_capability_mapping(self):
        self.assertEqual(
            (),
            capability_dependencies_for_node(
                effects=[{"op": "damage", "target": "$controller"}],
                target_schema=None,
                mechanic_ids=["cr-120-damage"],
            ),
        )

    def test_damage_aftermath_shape_requires_its_narrow_capability(self):
        dependencies = capability_dependencies_for_node(
            effects=[
                {
                    "op": "choose_damage_source",
                    "shield": {
                        "op": "create_damage_prevention_shield",
                        "aftermath": [{"kind": "deal_damage"}],
                    },
                }
            ],
            target_schema=None,
            mechanic_ids=["cr-615-prevention-effects"],
        )

        self.assertIn("damage.prevention.persistent_amount", dependencies)
        self.assertIn("damage.prevention.aftermath.damage", dependencies)


class GeneratedCapabilityTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_generated_bolt_uses_exact_trusted_capability_closure(self):
        record = _lightning_bolt_record()
        ir = compile_oracle_card(
            record,
            capability_registry=self.registry,
            capability_profile="commander_duel",
        )
        self.assertEqual("exact", ir.status)
        node = ir.faces[0].nodes[0]
        self.assertEqual(
            (
                "damage.amount.positive",
                "damage.result.multitype_permanent",
                "damage.result.player_life",
                "target.public.player_or_damageable_permanent",
            ),
            node.capability_dependencies,
        )
        self.assertIn("damage.result.battle_defense", node.capability_closure)
        self.assertNotIn("damage.result.infect", node.capability_closure)
        self.assertIsNotNone(node.capability_fingerprint)

        program = generated_programs(
            self.db,
            record,
            trust_level="trusted",
            capability_registry=self.registry,
            capability_profile="commander_duel",
        )[0]
        self.assertEqual("trusted", program.trust_level)
        self.assertFalse(program.requires_arbiter)
        self.assertEqual(
            list(node.capability_dependencies),
            program.capability_dependencies,
        )
        self.assertTrue(program.capability_closure["trusted"])
        self.assertEqual(
            "capability_closure_verified",
            program.provenance["dependency_trust"],
        )
        self.assertEqual(
            "capability_closure_verified",
            program.provenance["review_status"],
        )
        self.assertEqual(
            self.registry.fingerprint,
            program.provenance["capability_registry_fingerprint"],
        )
        self.assertEqual(
            program.to_dict(),
            SemanticProgram.from_dict(program.to_dict()).to_dict(),
        )
        semantic_schema = json.loads(
            (
                ROOT / "schemas" / "semantic-program.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(semantic_schema).validate(
            program.to_dict()
        )
        tampered = program.to_dict()
        tampered["capability_closure"]["trusted"] = False
        tampered["capability_closure"]["blockers"] = ["tampered"]
        with self.assertRaisesRegex(
            ValueError, "trusted unblocked capability closure"
        ):
            SemanticProgram.from_dict(tampered)

    def test_mutated_dependency_fails_closed_without_broad_family_wait(self):
        value = _registry_value()
        result = next(
            row
            for row in value["capabilities"]
            if row["id"] == "damage.result.player_life"
        )
        result["status"] = "blocked"
        result["blockers"] = ["test mutation"]
        mutated = CapabilityRegistry(value)
        record = _lightning_bolt_record()
        ir = compile_oracle_card(
            record,
            capability_registry=mutated,
            capability_profile="commander_duel",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)
        self.assertTrue(
            any(
                "damage.result.player_life" in blocker
                for blocker in ir.material_residuals[0].blockers
            )
        )
        with self.assertRaisesRegex(
            ValueError, "material Oracle residuals remain"
        ):
            generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=mutated,
                capability_profile="commander_duel",
            )

    def test_damage_aftermath_dependency_fails_closed(self):
        value = _registry_value()
        aftermath = next(
            row
            for row in value["capabilities"]
            if row["id"] == "damage.prevention.aftermath.damage"
        )
        aftermath["status"] = "blocked"
        aftermath["blockers"] = ["test mutation"]
        mutated = CapabilityRegistry(value)

        ir = compile_oracle_card(
            self.db.lookup("Deflecting Palm"),
            capability_registry=mutated,
            capability_profile="commander_review",
        )

        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "damage.prevention.aftermath.damage" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )

    def test_unrecognized_text_remains_residual_with_capability_registry(self):
        record = _lightning_bolt_record()
        changed = replace(
            record,
            oracle_text=record.oracle_text + " Then copy this spell.",
        )
        ir = compile_oracle_card(
            changed,
            capability_registry=self.registry,
            capability_profile="commander_duel",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_capability_profile_is_validated_even_for_textless_card(self):
        with self.assertRaisesRegex(ValueError, "Unknown capability profile"):
            compile_oracle_card(
                replace(_lightning_bolt_record(), oracle_text=""),
                capability_registry=self.registry,
                capability_profile="unknown-profile",
            )


if __name__ == "__main__":
    unittest.main()
