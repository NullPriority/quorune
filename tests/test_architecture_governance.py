from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from scripts.update_architecture_audit import (
    ROOT,
    _parent_map,
    _state_write_records,
    analyze_production,
)
from scripts.validate_architecture import (
    _load_json,
    _size_debt_failures,
    exception_binding_failures,
    module_classification_failures,
    source_policy_result,
    state_write_identity,
)


class ArchitectureGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = _load_json(ROOT / "platform" / "architecture-policy.json")
        cls.baseline = _load_json(
            ROOT / cls.policy["baseline"]
        )
        cls.source, cls.paths, cls.analyses = analyze_production()

    def test_exception_is_bound_to_exact_allowance_fingerprint(self):
        self.assertEqual(
            [], exception_binding_failures(self.policy, self.baseline)
        )
        changed = json.loads(json.dumps(self.baseline))
        changed["engine"]["logical_lines"] += 1
        failures = exception_binding_failures(self.policy, changed)
        self.assertTrue(failures)
        self.assertEqual(
            "architecture_exception_binding", failures[0]["guard"]
        )

    def test_source_policy_defers_only_generated_fingerprint_freshness(self):
        result = source_policy_result(
            {
                "status": "fail",
                "failures": [
                    {"guard": "module_classification_fingerprint"},
                    {"guard": "oversized_function_non_growth"},
                ],
            }
        )

        self.assertEqual("fail", result["status"])
        self.assertEqual(
            ["oversized_function_non_growth"],
            [row["guard"] for row in result["failures"]],
        )
        self.assertEqual(
            ["module_classification_fingerprint"],
            [
                row["guard"]
                for row in result["deferred_generated_failures"]
            ],
        )

    def test_new_production_module_is_default_denied(self):
        analyses = dict(self.analyses)
        analyses["quorune/unclassified.py"] = SimpleNamespace(
            module="quorune.unclassified",
            imports=(),
            tree=ast.parse(""),
        )
        failures = module_classification_failures(analyses, self.policy)
        failure = next(
            row
            for row in failures
            if row["guard"] == "module_classification_default_deny"
        )
        self.assertEqual(
            ["quorune/unclassified.py"],
            failure["evidence"]["unclassified"],
        )

    def test_identity_flow_scope_covers_all_production_python_modules(self):
        self.assertEqual(
            "all production Python modules",
            self.source["scope"]["identity_flow_scope"],
        )
        classified = {
            row["file"]
            for row in json.loads(
                (ROOT / "platform" / "module-classifications.json").read_text(
                    encoding="utf-8"
                )
            )["modules"]
        }
        self.assertEqual(set(self.analyses), classified)

    def test_state_write_identity_does_not_depend_on_source_line(self):
        relative = "quorune/example.py"
        source = {
            "scope": {
                "state_owner_modules": [relative],
                "state_parameter_modules": [],
            }
        }

        def identity(text: str):
            tree = ast.parse(text)
            records = _state_write_records(
                tree,
                tuple(text.splitlines(keepends=True)),
                relative,
                source,
                _parent_map(tree),
            )
            self.assertEqual(1, len(records))
            return state_write_identity(records[0])

        first = identity(
            "class Example:\n"
            "    def mutate(self):\n"
            "        self.state.winner = 'A'\n"
        )
        moved = identity(
            "\n\nclass Example:\n"
            "    def mutate(self):\n"
            "        self.state.winner = 'A'\n"
        )
        self.assertEqual(first, moved)
        self.assertEqual(
            (relative, "mutate", "assignment", "winner"), first
        )

    def test_existing_oversized_symbol_growth_fails(self):
        baseline = {
            "engine": {"logical_lines": 100},
            "oversized_modules": [
                {"file": "quorune/engine.py", "logical_lines": 100}
            ],
            "oversized_functions_and_methods": [
                {
                    "file": "quorune/engine.py",
                    "symbol": "CommanderEngine.large",
                    "logical_lines": 20,
                }
            ],
        }
        production = {
            "oversized_modules": [
                {"file": "quorune/engine.py", "logical_lines": 101}
            ],
            "oversized_functions_and_methods": [
                {
                    "file": "quorune/engine.py",
                    "symbol": "CommanderEngine.large",
                    "logical_lines": 21,
                }
            ],
        }
        failures = _size_debt_failures(
            {"review_thresholds": {"engine_net_logical_growth": 0}},
            baseline,
            production,
            {"logical_lines": 100},
        )
        self.assertEqual(
            {
                "oversized_module_non_growth",
                "oversized_function_non_growth",
            },
            {row["guard"] for row in failures},
        )


if __name__ == "__main__":
    unittest.main()
