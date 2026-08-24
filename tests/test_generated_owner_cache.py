from __future__ import annotations

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from scripts.generated_artifacts import (
    GeneratedArtifactInputGroups,
    GeneratorSpec,
    ROOT,
    load_input_groups,
    load_manifest,
)
from scripts.generated_owner_cache import (
    GeneratedOwnerCacheError,
    affected_owner_plan,
    compiler_identity_status,
    owner_input_identity,
    restore_owner_artifact,
    store_owner_artifact,
)
from scripts.cloud_generated_artifacts import run_owner
from scripts.find_reusable_workflow_artifact import find_reusable_run


class GeneratedOwnerCacheTests(unittest.TestCase):
    def _repository(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "owner-cache@example.invalid"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Owner Cache Test"],
            cwd=root,
            check=True,
        )

    def _fixture(self, root: Path):
        (root / "src").mkdir()
        (root / "scripts").mkdir()
        (root / "src" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "scripts" / "generate.py").write_text(
            "from __future__ import annotations\n",
            encoding="utf-8",
        )
        (root / "output.txt").write_text("generated\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
        spec = GeneratorSpec(
            id="fixture-owner",
            depends_on=(),
            outputs=("output.txt",),
            check=("scripts/generate.py", "--check"),
            write=("scripts/generate.py", "--write"),
            write_with_database=None,
            write_policy="automatic",
            input_groups=("source",),
            implementation_inputs=("scripts/generate.py",),
            database_identity="none",
            execution_class="foundation",
            reuse_policy="safe",
        )
        groups = GeneratedArtifactInputGroups(
            groups=(("source", ("src/**/*.py",)),)
        )
        return spec, groups

    def test_owner_input_identity_is_content_bound_not_commit_bound(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            self._repository(root)
            spec, groups = self._fixture(root)
            first = owner_input_identity(
                spec,
                specs=(spec,),
                input_groups=groups,
                root=root,
                database=None,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-qm", "metadata only"],
                cwd=root,
                check=True,
            )
            second = owner_input_identity(
                spec,
                specs=(spec,),
                input_groups=groups,
                root=root,
                database=None,
            )
            self.assertEqual(first.fingerprint, second.fingerprint)

            (root / "src" / "value.py").write_text(
                "VALUE = 2\n", encoding="utf-8"
            )
            changed = owner_input_identity(
                spec,
                specs=(spec,),
                input_groups=groups,
                root=root,
                database=None,
            )
            self.assertNotEqual(first.fingerprint, changed.fingerprint)

    def test_owner_artifact_round_trip_fails_closed_on_corruption(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            self._repository(root)
            spec, groups = self._fixture(root)
            identity = owner_input_identity(
                spec,
                specs=(spec,),
                input_groups=groups,
                root=root,
                database=None,
            )
            artifact = root / "cache" / identity.fingerprint
            stored = store_owner_artifact(
                spec,
                identity,
                artifact_dir=artifact,
                root=root,
            )
            (root / "output.txt").write_text("stale\n", encoding="utf-8")
            restored = restore_owner_artifact(
                spec,
                identity,
                artifact_dir=artifact,
                root=root,
            )
            self.assertEqual(stored, restored)
            self.assertEqual("generated\n", (root / "output.txt").read_text())

            (artifact / "output.txt").write_text("corrupt\n", encoding="utf-8")
            with self.assertRaisesRegex(
                GeneratedOwnerCacheError,
                "missing or corrupt",
            ):
                restore_owner_artifact(
                    spec,
                    identity,
                    artifact_dir=artifact,
                    root=root,
                )

    def test_repository_manifest_declares_safe_input_closures(self):
        specs = load_manifest()
        groups = load_input_groups()
        cacheable = [spec for spec in specs if spec.reuse_policy == "safe"]

        self.assertGreaterEqual(len(cacheable), 10)
        self.assertTrue(groups.groups)
        for spec in cacheable:
            self.assertTrue(spec.input_groups or spec.input_paths)
            self.assertTrue(spec.implementation_inputs)
            self.assertNotEqual("manual", spec.execution_class)
        architecture = next(
            spec for spec in specs if spec.id == "architecture-audit"
        )
        compiler_corpus = next(
            spec for spec in specs if spec.id == "compiler-corpus-coverage"
        )
        self.assertIn(
            "quorune/semantic_packs/**/*.json",
            groups.patterns("rules-source"),
        )
        self.assertIn("rules-source", architecture.input_groups)
        self.assertIn("rules-source", compiler_corpus.input_groups)
        self.assertIn("tests-source", architecture.input_groups)
        self.assertIn("web/tests/**/*.ts", architecture.input_paths)

    def test_automatic_plan_inherits_unchanged_owner_without_generation(self):
        selected = GeneratorSpec(
            id="selected-owner",
            depends_on=(),
            outputs=("selected.txt",),
            check=("selected.py", "--check"),
            write=("selected.py", "--write"),
            write_with_database=None,
            write_policy="automatic",
            input_groups=("source",),
            implementation_inputs=("selected.py",),
            database_identity="none",
            execution_class="foundation",
            reuse_policy="safe",
        )
        identity = mock.Mock(fingerprint="a" * 64)
        artifact_receipt = mock.Mock()
        artifact_receipt.to_dict.return_value = {"owner": "selected-owner"}
        local = ROOT / "local"
        local.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=local) as raw, mock.patch(
            "scripts.cloud_generated_artifacts.load_manifest",
            return_value=(selected,),
        ), mock.patch(
            "scripts.cloud_generated_artifacts._owner_identity",
            return_value=identity,
        ), mock.patch(
            "scripts.cloud_generated_artifacts.owner_cache_directory",
            return_value=Path(raw) / "cache-miss",
        ), mock.patch(
            "scripts.cloud_generated_artifacts.store_owner_artifact",
            return_value=artifact_receipt,
        ), mock.patch(
            "scripts.cloud_generated_artifacts._run"
        ) as runner, mock.patch(
            "scripts.cloud_generated_artifacts._copy_outputs",
            return_value={"selected.txt": "hash"},
        ), mock.patch(
            "scripts.cloud_generated_artifacts._source_commit",
            return_value="b" * 40,
        ), mock.patch(
            "scripts.cloud_generated_artifacts.tracked_worktree_source_fingerprint",
            return_value="source",
        ), mock.patch(
            "scripts.cloud_generated_artifacts._snapshot_metadata",
            return_value={},
        ):
            receipt = run_owner(
                "selected-owner",
                str(Path(raw) / "stage"),
                None,
                affected_owners_json="[]",
            )

        self.assertEqual("inherited", receipt["execution"])
        self.assertEqual(
            ["check:selected-owner"],
            [call.args[0] for call in runner.call_args_list],
        )

    def test_migration_plan_and_compiler_sentinel_fail_conservatively(self):
        with mock.patch(
            "scripts.generated_owner_cache._manifest_at_ref",
            side_effect=GeneratedOwnerCacheError(
                "legacy manifest has no input closure"
            ),
        ):
            plan = affected_owner_plan(base_ref="origin/main", root=ROOT)
        sentinel = compiler_identity_status(base_ref="origin/main", root=ROOT)

        self.assertEqual(
            "base-manifest-has-no-input-closure",
            plan["reason"],
        )
        self.assertTrue(plan["database_required"])
        self.assertIn("compiler-corpus-coverage", plan["owners"])
        self.assertTrue(sentinel["ok"], json.dumps(sentinel, sort_keys=True))

    def test_remote_reuse_requires_completed_same_repository_workflow(self):
        responses = {
            "repos/acme/quorune/actions/artifacts?name=owner-key&per_page=100": {
                "artifacts": [
                    {
                        "created_at": "2026-08-21T03:00:00Z",
                        "expired": False,
                        "workflow_run": {"id": 30},
                    },
                    {
                        "created_at": "2026-08-21T02:00:00Z",
                        "expired": False,
                        "workflow_run": {"id": 20},
                    },
                    {
                        "created_at": "2026-08-21T01:00:00Z",
                        "expired": False,
                        "workflow_run": {"id": 10},
                    },
                ]
            },
            "repos/acme/quorune/actions/runs/30": {
                "status": "in_progress",
                "conclusion": None,
                "path": ".github/workflows/generated-artifacts.yml",
                "head_repository": {"full_name": "acme/quorune"},
            },
            "repos/acme/quorune/actions/runs/20": {
                "status": "completed",
                "conclusion": "success",
                "path": ".github/workflows/generated-artifacts.yml",
                "head_repository": {"full_name": "fork/quorune"},
            },
            "repos/acme/quorune/actions/runs/10": {
                "status": "completed",
                "conclusion": "failure",
                "path": ".github/workflows/generated-artifacts.yml",
                "head_repository": {"full_name": "acme/quorune"},
            },
        }

        self.assertEqual(
            10,
            find_reusable_run(
                artifact_name="owner-key",
                repository="acme/quorune",
                fetch_json=responses.__getitem__,
            ),
        )


if __name__ == "__main__":
    unittest.main()
