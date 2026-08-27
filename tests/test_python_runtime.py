from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
import unittest
import zipfile

from quorune.python_runtime import (
    UnsupportedPythonRuntime,
    require_supported_python,
    require_supported_runtime,
)
from scripts.validate_python_runtime import (
    project_policy_failures,
    validate,
    workflow_policy_failures,
)
from scripts.verify_wheel import _assert_namespace_contents, _requires_python_matches


@dataclass(frozen=True)
class FakeVersion:
    major: int
    minor: int


class PythonRuntimeTests(unittest.TestCase):
    def test_current_runtime_and_repository_policy_pass(self):
        result = validate()
        self.assertTrue(result["ok"])
        self.assertEqual("3.12", result["required_minor"])

    def test_older_and_newer_minor_versions_fail_closed(self):
        for version in (FakeVersion(3, 11), FakeVersion(3, 13), FakeVersion(4, 0)):
            with self.subTest(version=version):
                with self.assertRaises(UnsupportedPythonRuntime):
                    require_supported_python(version)

    def test_exact_supported_minor_is_accepted(self):
        require_supported_python(FakeVersion(3, 12))

    def test_non_cpython_and_32_bit_runtimes_fail_closed(self):
        with self.assertRaises(UnsupportedPythonRuntime):
            require_supported_runtime(FakeVersion(3, 12), implementation="PyPy")
        with self.assertRaises(UnsupportedPythonRuntime):
            require_supported_runtime(FakeVersion(3, 12), maxsize=2**31 - 1)

    def test_project_policy_reports_every_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".python-version").write_text("3.11\n", encoding="utf-8")
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'fixture'\nversion = '1.0'\n"
                "requires-python = '>=3.11'\nclassifiers = []\n",
                encoding="utf-8",
            )
            failures = project_policy_failures(root)

        self.assertEqual(5, len(failures))
        self.assertTrue(any(".python-version" in item for item in failures))
        self.assertTrue(any("requires-python" in item for item in failures))
        self.assertEqual(
            3,
            sum("classifier is missing" in item for item in failures),
        )

    def test_current_workflows_pin_every_setup_to_x64_312(self):
        self.assertEqual([], workflow_policy_failures())

    def test_provenance_validating_workflows_fetch_complete_history(self):
        workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for workflow_name in ("nightly.yml",):
            with self.subTest(workflow=workflow_name):
                workflow = (workflows / workflow_name).read_text(encoding="utf-8")
                checkout = workflow.split("actions/checkout@v4", 1)[1].split(
                    "actions/setup-python@v5", 1
                )[0]
                self.assertIn("fetch-depth: 0", checkout)

        main_smoke = (workflows / "main-smoke.yml").read_text(encoding="utf-8")
        checkout = main_smoke.split("actions/checkout@v4", 1)[1].split(
            "actions/setup-python@v5", 1
        )[0]
        self.assertIn("fetch-depth: 1", checkout)
        self.assertIn(
            "python scripts/certification_receipt.py verify-main",
            main_smoke,
        )

        ci = (workflows / "ci.yml").read_text(encoding="utf-8")
        certification = ci.split("  certification:\n", 1)[1].split(
            "\n  metrics:\n", 1
        )[0]
        checkout = certification.split("actions/checkout@v4", 1)[1].split(
            "Require every PR gate", 1
        )[0]
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha }}",
            checkout,
        )
        self.assertIn("fetch-depth: 1", checkout)

        windows_full = ci.split("  windows_full:\n", 1)[1].split(
            "\n  windows_package:\n", 1
        )[0]
        checkout = windows_full.split("actions/checkout@v4", 1)[1].split(
            "actions/setup-python@v5", 1
        )[0]
        self.assertIn("fetch-depth: 0", checkout)

        nightly = (workflows / "nightly.yml").read_text(encoding="utf-8")
        mutation_and_soak = nightly.split("  mutation-and-soak:\n", 1)[1].split(
            "\n  corpus:\n", 1
        )[0]
        checkout = mutation_and_soak.split("actions/checkout@v4", 1)[1].split(
            "actions/setup-python@v5", 1
        )[0]
        self.assertIn("fetch-depth: 0", checkout)

    def test_workflow_policy_accepts_variable_job_counts_and_fails_missing_pin(self):
        setup = (
            "steps:\n"
            "  - uses: actions/setup-python@v5\n"
            "    with:\n"
            "      python-version: '3.12'\n"
            "      architecture: x64\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            for name in (
                "ci.yml",
                "live-integration.yml",
                "main-smoke.yml",
                "nightly.yml",
            ):
                (workflows / name).write_text(setup, encoding="utf-8")
            (workflows / "ci.yml").write_text(setup * 7, encoding="utf-8")
            self.assertEqual([], workflow_policy_failures(root))

            (workflows / "nightly.yml").write_text(
                setup.replace("      architecture: x64\n", ""),
                encoding="utf-8",
            )
            failures = workflow_policy_failures(root)

        self.assertEqual(1, len(failures))
        self.assertIn("nightly.yml must configure x64", failures[0])

    def test_workflow_policy_ignores_architecture_job_key(self):
        setup = (
            "jobs:\n"
            "  generated:\n"
            "    steps:\n"
            "      - uses: actions/setup-python@v5\n"
            "        with:\n"
            "          python-version: '3.12'\n"
            "          architecture: x64\n"
            "  architecture:\n"
            "    runs-on: ubuntu-latest\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            for name in (
                "ci.yml",
                "live-integration.yml",
                "main-smoke.yml",
                "nightly.yml",
            ):
                (workflows / name).write_text(setup, encoding="utf-8")

            failures = workflow_policy_failures(root)

        self.assertEqual([], failures)

    def test_wheel_requirement_accepts_canonicalized_order_only(self):
        self.assertTrue(_requires_python_matches("<3.13,>=3.12"))
        self.assertFalse(_requires_python_matches(">=3.11"))
        self.assertFalse(_requires_python_matches(None))

    def test_wheel_rejects_retired_namespace_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "fixture.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("quorune/__init__.py", "")
                archive.writestr("mtg_commander_sim/__init__.py", "")
            with self.assertRaises(SystemExit):
                _assert_namespace_contents(wheel)

            clean = Path(temporary) / "clean.whl"
            with zipfile.ZipFile(clean, "w") as archive:
                archive.writestr("quorune/__init__.py", "")
            _assert_namespace_contents(clean)


if __name__ == "__main__":
    unittest.main()
