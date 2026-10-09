from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quorune.rules.capabilities import (
    CapabilityRegistry,
    CapabilityRegistryError,
    load_default_capability_registry,
)
from quorune.rules.evidence import (
    CapabilityEvidenceError,
    capability_evidence_fingerprint,
    load_capability_evidence_index,
    validate_capability_evidence_index,
)
from scripts.update_capability_evidence import (
    DECLARATIONS_PATH,
    EvidenceGenerationError,
    REGISTRY_PATH,
    ROOT,
    build_index,
    discover_tests,
)


class CapabilityEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        cls.declaration_source = json.loads(
            DECLARATIONS_PATH.read_text(encoding="utf-8")
        )
        cls.discovered = discover_tests(ROOT)

    def test_default_registry_is_bound_to_current_generated_evidence(self):
        registry = load_default_capability_registry()
        value, fingerprint = load_capability_evidence_index(
            registry=registry
        )
        self.assertEqual(fingerprint, registry.evidence_fingerprint)
        self.assertEqual(fingerprint, value["fingerprint"])
        closure = registry.closure(
            ["damage.result.player_life"], profile="commander_duel"
        )
        self.assertTrue(closure.trusted)
        self.assertEqual(fingerprint, closure.evidence_fingerprint)

    def test_unverified_registry_fails_closed(self):
        registry = CapabilityRegistry(self.registry_value)
        closure = registry.closure(
            ["damage.result.player_life"], profile="commander_duel"
        )
        self.assertFalse(closure.trusted)
        self.assertIn("evidence_index:unverified", closure.blockers)

    def test_evidence_validation_reuses_one_defensive_capability_snapshot(self):
        registry = CapabilityRegistry(self.registry_value)
        value = json.loads((ROOT / "quorune/rules/capability-evidence.json").read_text())
        with patch.object(registry, "capabilities", wraps=registry.capabilities) as snapshot, patch.object(
            registry, "capability", side_effect=AssertionError("repeated per-declaration copy")
        ):
            self.assertEqual(value["fingerprint"], validate_capability_evidence_index(value, registry=registry))
            snapshot.assert_called_once_with()
        changed = deepcopy(value)
        changed["declarations"][0]["capability_id"] = "missing.capability"
        with self.assertRaisesRegex(CapabilityEvidenceError, "Unknown capability evidence target"):
            validate_capability_evidence_index(changed, registry=registry)
        stale_registry = CapabilityRegistry({**self.registry_value, "registry_version": self.registry_value["registry_version"] + 1})
        with self.assertRaisesRegex(CapabilityEvidenceError, "registry fingerprint is stale"):
            validate_capability_evidence_index(value, registry=stale_registry)

    def test_removed_or_renamed_test_invalidates_declaration(self):
        source = deepcopy(self.declaration_source)
        source["declarations"][0]["test_id"] += "_renamed"
        with self.assertRaisesRegex(
            EvidenceGenerationError, "Removed or renamed evidence test"
        ):
            build_index(
                registry_value=self.registry_value,
                declaration_source=source,
                discovered_test_ids=set(self.discovered.values()),
            )

    def test_registry_validation_test_cannot_substitute_for_behavior(self):
        source = deepcopy(self.declaration_source)
        source["declarations"][0]["test_id"] = (
            "tests.test_rule_capabilities.CapabilityRegistryTests."
            "test_registry_matches_schema_rules_snapshot_and_test_evidence"
        )
        with self.assertRaisesRegex(
            EvidenceGenerationError,
            "Registry-validation tests cannot be behavioral evidence",
        ):
            build_index(
                registry_value=self.registry_value,
                declaration_source=source,
                discovered_test_ids=set(self.discovered.values()),
            )

    def test_duplicate_and_wrong_rule_declarations_fail(self):
        registry = CapabilityRegistry(self.registry_value)
        source = deepcopy(self.declaration_source)
        index = build_index(
            registry_value=self.registry_value,
            declaration_source=source,
            discovered_test_ids=set(self.discovered.values()),
        )
        duplicate = deepcopy(index)
        duplicate["declarations"].append(
            deepcopy(duplicate["declarations"][0])
        )
        with self.assertRaisesRegex(
            CapabilityEvidenceError, "Duplicate capability evidence"
        ):
            validate_capability_evidence_index(
                duplicate, registry=registry
            )

        source = deepcopy(self.declaration_source)
        source["declarations"][0]["official_rule_ids"] = ["999.999"]
        with self.assertRaisesRegex(
            EvidenceGenerationError, "unknown official rule"
        ):
            build_index(
                registry_value=self.registry_value,
                declaration_source=source,
                discovered_test_ids=set(self.discovered.values()),
            )

    def test_trusted_status_rejects_conflated_or_surviving_mutation(self):
        value = deepcopy(self.registry_value)
        row = next(
            item
            for item in value["capabilities"]
            if item["id"] == "damage.result.player_life"
        )
        row["implementation_mutation_status"] = "survived"
        with self.assertRaisesRegex(
            CapabilityRegistryError, "killed implementation mutation"
        ):
            CapabilityRegistry(value)

        value = deepcopy(self.registry_value)
        row = next(
            item
            for item in value["capabilities"]
            if item["id"] == "damage.result.player_life"
        )
        row["dependency_fail_closed_status"] = "not_run"
        with self.assertRaisesRegex(
            CapabilityRegistryError, "passed dependency fail-closed"
        ):
            CapabilityRegistry(value)

    def test_minimum_trusted_evidence_cannot_be_waived_by_registry_row(self):
        target_id = "damage.result.player_life"
        registry = CapabilityRegistry(self.registry_value)
        index = build_index(
            registry_value=self.registry_value,
            declaration_source=self.declaration_source,
            discovered_test_ids=set(self.discovered.values()),
        )
        for evidence_class in ("positive", "negative", "replay", "mutation"):
            with self.subTest(evidence_class=evidence_class):
                changed = deepcopy(index)
                changed["declarations"] = [
                    row
                    for row in changed["declarations"]
                    if not (
                        row["capability_id"] == target_id
                        and row["evidence_class"] == evidence_class
                    )
                ]
                changed["fingerprint"] = capability_evidence_fingerprint(
                    changed
                )
                with self.assertRaisesRegex(
                    CapabilityEvidenceError,
                    rf"{target_id} lacks explicit evidence:.*{evidence_class}",
                ):
                    validate_capability_evidence_index(
                        changed, registry=registry
                    )

    def test_required_evidence_empty_and_unresolvable_component_fail_trust(self):
        value = deepcopy(self.registry_value)
        row = next(
            item
            for item in value["capabilities"]
            if item["id"] == "damage.result.player_life"
        )
        row["required_evidence"] = []
        with self.assertRaisesRegex(
            CapabilityRegistryError, "must require minimum evidence"
        ):
            CapabilityRegistry(value)

        value = deepcopy(self.registry_value)
        row = next(
            item
            for item in value["capabilities"]
            if item["id"] == "damage.result.player_life"
        )
        row["implementation_components"] = ["missing.module.Symbol"]
        with self.assertRaisesRegex(
            CapabilityRegistryError, "resolvable implementation component"
        ):
            CapabilityRegistry(value)

    def test_required_evidence_must_cover_every_supported_profile(self):
        registry = CapabilityRegistry(self.registry_value)
        index = build_index(
            registry_value=self.registry_value,
            declaration_source=self.declaration_source,
            discovered_test_ids=set(self.discovered.values()),
        )
        target_id = "damage.result.player_life"
        changed = deepcopy(index)
        for row in changed["declarations"]:
            if (
                row["capability_id"] == target_id
                and row["evidence_class"] == "positive"
            ):
                row["supported_profiles"] = ["commander_duel"]
        changed["fingerprint"] = capability_evidence_fingerprint(changed)
        with self.assertRaisesRegex(
            CapabilityEvidenceError,
            "positive evidence does not cover supported profiles",
        ):
            validate_capability_evidence_index(changed, registry=registry)


if __name__ == "__main__":
    unittest.main()
