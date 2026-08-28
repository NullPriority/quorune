from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from scripts.change_impact import classify_changes
from scripts.ci_policy_delta import (
    compare_change_impact_policy,
    compare_test_shards,
)
from scripts.ci_risk_sentinel import requires_high_risk_gate


ROOT = Path(__file__).resolve().parents[1]


class CiPolicyDeltaTests(unittest.TestCase):
    def test_impact_policy_accepts_only_monotonic_selection_evidence(self):
        base = json.loads(
            (ROOT / "platform/change-impact-policy.json").read_text(
                encoding="utf-8"
            )
        )
        additive = deepcopy(base)
        additive["path_rules"][0].setdefault("test_modules", []).append(
            "test_ci_policy_delta"
        )
        self.assertTrue(compare_change_impact_policy(base, additive).additive)

        removed = deepcopy(additive)
        removed["path_rules"][0]["test_modules"] = []
        delta = compare_change_impact_policy(additive, removed)
        self.assertFalse(delta.additive)
        self.assertIn(
            "reduced-selection:path_rules:changed-python-test:test_modules",
            delta.reasons,
        )

        downgraded = deepcopy(base)
        downgraded["risk_rules"][1]["risk_class"] = "ordinary_source"
        self.assertFalse(
            compare_change_impact_policy(base, downgraded).additive
        )

    def test_shard_policy_accepts_new_tests_but_rejects_moves_and_removals(self):
        base = json.loads(
            (ROOT / "platform/test-shards.json").read_text(encoding="utf-8")
        )
        prior = deepcopy(base)
        prior["primary_shards"]["generated-validation"].remove(
            "test_ci_policy_delta"
        )
        self.assertTrue(
            compare_test_shards(
                prior,
                base,
                added_paths=("tests/test_ci_policy_delta.py",),
            ).additive
        )
        self.assertFalse(compare_test_shards(prior, base).additive)

        moved = deepcopy(base)
        moved["primary_shards"]["generated-validation"].remove(
            "test_ci_policy_delta"
        )
        moved["primary_shards"]["functional-01"].append(
            "test_ci_policy_delta"
        )
        self.assertFalse(compare_test_shards(base, moved).additive)

        reduced_overlay = deepcopy(base)
        suite = next(iter(reduced_overlay["overlay_suites"]))
        reduced_overlay["overlay_suites"][suite].pop()
        self.assertFalse(compare_test_shards(base, reduced_overlay).additive)

    def test_base_sentinel_can_exempt_only_verified_policy_paths(self):
        path = "platform/change-impact-policy.json"
        self.assertTrue(requires_high_risk_gate((path,)))
        self.assertEqual(
            (),
            requires_high_risk_gate(
                (path,), additive_selection_paths=(path,)
            ),
        )
        self.assertTrue(
            requires_high_risk_gate(
                ("scripts/ci_policy_delta.py", path),
                additive_selection_paths=(path,),
            )
        )

    def test_classifier_keeps_verified_additions_ordinary(self):
        path = "platform/change-impact-policy.json"
        plan = classify_changes(
            (path,),
            additive_selection_paths=(path,),
        )
        self.assertEqual("ordinary_source", plan.risk_class)
        self.assertIn(
            f"additive-selection-authority:{path}", plan.risk_reasons
        )
        with self.assertRaisesRegex(ValueError, "unsupported authority"):
            classify_changes(
                ("scripts/ci_plan.py",),
                additive_selection_paths=("scripts/ci_plan.py",),
            )


if __name__ == "__main__":
    unittest.main()
