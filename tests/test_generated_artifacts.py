from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from scripts.finalize_generated import (
    POST_CHECKS,
    check_all,
    check_assembled,
    changed_generated_outputs,
    stabilization_ids,
    write_until_stable,
)
from scripts.cloud_generated_artifacts import (
    CloudGeneratedArtifactError,
    _git_blob_oid,
    install_bundle,
    run_owner,
    stage_bundle,
)
from scripts.update_compiler_corpus_coverage import (
    CompilerCorpusCoverageError,
    validate_reports,
)
from scripts.update_rules_derived import _normalized_generated_bytes
from scripts.generated_artifacts import (
    GeneratedArtifactDiscoverySpec,
    GeneratedArtifactManifestError,
    GeneratorSpec,
    ROOT,
    all_outputs,
    discover_tracked_generated_artifacts,
    load_manifest,
    parse_discovery,
    parse_manifest,
    topological_order,
    validate_manifest_completeness,
)
from scripts.demo_four_player_protocol import validate_protocol_output
from scripts.install_dev_hooks import (
    HookInstallationError,
    check as check_hooks,
    install as install_hooks,
)
from scripts.validate_generated_web_types import validate as validate_web_types
from quorune.oracle_ir import ORACLE_COMPILER_VERSION
from quorune.compiler.target_effect_corpus_assurance import (
    SEQUENCE_TEMPLATE_ID,
    STANDALONE_TEMPLATE_ID,
    TargetEffectCorpusCollector,
)
from quorune.rules.capabilities import load_default_capability_registry


class GeneratedArtifactFinalizationTests(unittest.TestCase):
    def test_rules_derived_normalizes_platform_line_endings(self):
        with TemporaryDirectory() as raw:
            output = Path(raw) / "generated.json"
            output.write_bytes(b'{"ok":true}\r\n')

            self.assertEqual(
                b'{"ok":true}\n',
                _normalized_generated_bytes(output),
            )

    def test_cloud_installer_compares_git_normalized_line_endings(self):
        local = ROOT / "local"
        local.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=local) as raw:
            directory = Path(raw)
            lf = directory / "lf.json"
            crlf = directory / "crlf.json"
            lf.write_bytes(b'{"ok":true}\n')
            crlf.write_bytes(b'{"ok":true}\r\n')

            self.assertNotEqual(lf.read_bytes(), crlf.read_bytes())
            self.assertEqual(
                _git_blob_oid(lf, "coverage/mechanics-coverage.json"),
                _git_blob_oid(crlf, "coverage/mechanics-coverage.json"),
            )

    def test_cloud_owner_relies_on_dag_without_broad_dependency_checks(self):
        dependency = GeneratorSpec(
            id="manual-upstream",
            depends_on=(),
            outputs=("upstream.txt",),
            check=("upstream.py", "--check"),
            write=None,
            write_with_database=None,
            write_policy="manual",
        )
        selected = GeneratorSpec(
            id="selected-owner",
            depends_on=("manual-upstream",),
            outputs=("selected.txt",),
            check=("selected.py", "--check"),
            write=("selected.py", "--write"),
            write_with_database=None,
            write_policy="automatic",
        )
        local = ROOT / "local"
        local.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=local) as raw, mock.patch(
            "scripts.cloud_generated_artifacts.load_manifest",
            return_value=(dependency, selected),
        ), mock.patch(
            "scripts.cloud_generated_artifacts._run"
        ) as runner, mock.patch(
            "scripts.cloud_generated_artifacts._copy_outputs",
            return_value={"selected.txt": "hash"},
        ), mock.patch(
            "scripts.cloud_generated_artifacts._source_commit",
            return_value="a" * 40,
        ), mock.patch(
            "scripts.cloud_generated_artifacts.tracked_worktree_source_fingerprint",
            return_value="source-fingerprint",
        ), mock.patch(
            "scripts.cloud_generated_artifacts._snapshot_metadata",
            return_value={},
        ):
            receipt = run_owner(
                "selected-owner",
                str(Path(raw) / "selected"),
                None,
            )

        self.assertEqual(
            ["selected-owner", "check:selected-owner"],
            [call.args[0] for call in runner.call_args_list],
        )
        self.assertEqual("selected-owner", receipt["owner"])

    def test_cloud_bundle_round_trip_is_hash_and_commit_bound(self):
        local = ROOT / "local"
        local.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=local) as raw:
            stage = Path(raw) / "bundle"
            receipt = stage_bundle(str(stage))
            installed = install_bundle(
                str(stage),
                str(receipt["source_commit"]),
                write_receipt=False,
            )

            self.assertEqual(len(all_outputs(load_manifest())), receipt["output_count"])
            self.assertEqual([], installed["changed_outputs"])

            receipt_path = ROOT / "local" / "imported-receipt.json"
            with mock.patch(
                "scripts.cloud_generated_artifacts.write_finalization_receipt",
                return_value=(receipt_path, object()),
            ) as writer:
                imported = install_bundle(
                    str(stage),
                    str(receipt["source_commit"]),
                )
            writer.assert_called_once()
            self.assertEqual(
                str(receipt_path), imported["finalization_receipt"]
            )

            relative = sorted(receipt["outputs"])[0]
            (stage / relative).write_bytes(b"corrupt")
            with self.assertRaisesRegex(
                CloudGeneratedArtifactError,
                "missing or corrupt",
            ):
                install_bundle(
                    str(stage),
                    str(receipt["source_commit"]),
                    write_receipt=False,
                )

    def test_cloud_bundle_staging_refuses_paths_outside_local(self):
        with TemporaryDirectory() as outside:
            with self.assertRaisesRegex(
                CloudGeneratedArtifactError,
                "below local",
            ):
                stage_bundle(str(Path(outside) / "bundle"))

    def test_compiler_corpus_reports_fail_closed_on_stale_source_or_counts(self):
        capabilities = load_default_capability_registry()
        snapshot = {"oracle_source_sha256": "a" * 64}

        def oracle(commander_only: bool, count: int) -> dict:
            return {
                "compiler_version": ORACLE_COMPILER_VERSION,
                "capability_profile": "commander_review",
                "capability_registry_fingerprint": capabilities.fingerprint,
                "capability_evidence_fingerprint": (
                    capabilities.evidence_fingerprint
                ),
                "card_data_snapshot": snapshot,
                "commander_legal_only": commander_only,
                "total_oracle_ids": count,
                "status_counts": {"exact": count},
                "target_effect_corpus_assurance": (
                    TargetEffectCorpusCollector().report(
                        compiler_version=ORACLE_COMPILER_VERSION,
                        capability_registry=capabilities,
                        capability_profile="commander_review",
                        card_data_snapshot=snapshot,
                        commander_legal_only=commander_only,
                    )
                ),
            }

        def program(commander_only: bool, count: int) -> dict:
            return {
                "compiler_version": ORACLE_COMPILER_VERSION,
                "profile": "commander_review",
                "capability_registry_fingerprint": capabilities.fingerprint,
                "capability_evidence_fingerprint": (
                    capabilities.evidence_fingerprint
                ),
                "card_data_snapshot": snapshot,
                "commander_legal_only": commander_only,
                "cards_considered": count,
                "status_counts": {"trusted": count},
            }

        reports = {
            "oracle_full": oracle(False, 4),
            "oracle_commander": oracle(True, 3),
            "program_full": program(False, 4),
            "program_commander": program(True, 3),
        }
        validate_reports(reports)

        stale = copy.deepcopy(reports)
        stale["program_commander"]["compiler_version"] = "oracle-ir-stale"
        with self.assertRaisesRegex(
            CompilerCorpusCoverageError, "compiler version is stale"
        ):
            validate_reports(stale)

        mismatched = copy.deepcopy(reports)
        mismatched["program_commander"]["cards_considered"] = 2
        with self.assertRaisesRegex(
            CompilerCorpusCoverageError, "card counts are inconsistent"
        ):
            validate_reports(mismatched)

        stale_assurance = copy.deepcopy(reports)
        stale_assurance["oracle_commander"][
            "target_effect_corpus_assurance"
        ]["grammar_source_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(
            CompilerCorpusCoverageError,
            "fingerprint is stale",
        ):
            validate_reports(stale_assurance)

    def test_tracked_target_effect_assurance_is_corpus_derived_and_nonempty(self):
        report = json.loads(
            (ROOT / "coverage" / "oracle-coverage-commander.json").read_text(
                encoding="utf-8"
            )
        )
        assurance = report["target_effect_corpus_assurance"]
        dimensions = assurance["dimensions"]

        self.assertGreater(assurance["total_nodes"], 0)
        self.assertGreater(assurance["shape_count"], 0)
        self.assertEqual(
            {STANDALONE_TEMPLATE_ID, SEQUENCE_TEMPLATE_ID},
            set(dimensions["templates"]),
        )
        self.assertEqual(
            assurance["total_nodes"],
            sum(dimensions["templates"].values()),
        )
        self.assertEqual(
            assurance["total_nodes"],
            sum(shape["count"] for shape in assurance["shapes"]),
        )
        self.assertTrue(
            all(shape["representative_identities"] for shape in assurance["shapes"])
        )

    def test_generated_manifest_has_one_owner_and_dependency_order(self):
        specs = load_manifest()
        ordered = [spec.id for spec in topological_order(specs)]
        outputs = all_outputs(specs)

        self.assertEqual(len(outputs), len(set(outputs)))
        self.assertTrue(
            {
                "architecture-audit",
                "browser-protocol-bindings",
                "capability-evidence",
                "card-unlock-frontier",
                "ci-escape-report",
                "compiler-corpus-coverage",
                "continuous-effect-performance",
                "module-classifications",
                "pinned-rules-snapshot",
                "platform-status",
                "protocol-reference",
                "protocol-smoke-fixture",
                "reusable-pieces",
                "rules-derived",
                "rules-scheduler",
                "work-selection-cohort-measurements",
            }.issubset({spec.id for spec in specs})
        )
        self.assertLess(
            ordered.index("pinned-rules-snapshot"),
            ordered.index("rules-derived"),
        )
        self.assertLess(
            ordered.index("rules-derived"),
            ordered.index("rules-scheduler"),
        )
        self.assertLess(
            ordered.index("rules-derived"),
            ordered.index("compact-ci-card-dependencies"),
        )
        self.assertLess(
            ordered.index("rules-derived"),
            ordered.index("platform-status"),
        )
        self.assertLess(
            ordered.index("compiler-corpus-coverage"),
            ordered.index("card-unlock-frontier"),
        )
        self.assertLess(
            ordered.index("card-unlock-frontier"),
            ordered.index("work-selection-cohort-measurements"),
        )
        self.assertLess(
            ordered.index("work-selection-cohort-measurements"),
            ordered.index("rules-scheduler"),
        )
        self.assertLess(
            ordered.index("compiler-corpus-coverage"),
            ordered.index("platform-status"),
        )
        self.assertLess(
            ordered.index("platform-status"),
            ordered.index("architecture-audit"),
        )
        self.assertLess(
            ordered.index("module-classifications"),
            ordered.index("architecture-audit"),
        )
        self.assertLess(
            ordered.index("reusable-pieces"),
            ordered.index("architecture-audit"),
        )
        rules_owner = next(spec for spec in specs if spec.id == "rules-derived")
        self.assertIn("rules/conformance-cases.json", rules_owner.outputs)
        self.assertIn("mechanics/registry.json", rules_owner.outputs)
        self.assertIn("coverage/rules-coverage.md", rules_owner.outputs)
        architecture_owner = next(
            spec for spec in specs if spec.id == "architecture-audit"
        )
        self.assertNotIn(
            "platform/card-name-hash-index.json", architecture_owner.outputs
        )
        self.assertIsNone(architecture_owner.write_with_database)

    def test_every_discovered_generated_artifact_has_one_existing_owner(self):
        manifest = json.loads(
            (ROOT / "platform" / "generated-artifacts.json").read_text(
                encoding="utf-8"
            )
        )
        specs = parse_manifest(manifest)
        discovery = parse_discovery(manifest["discovery"])
        report = validate_manifest_completeness(
            specs,
            discovery,
            root=ROOT,
        )
        owners = dict(report.owners)

        self.assertEqual(
            report.discovered,
            discover_tracked_generated_artifacts(ROOT, discovery),
        )
        self.assertEqual(set(report.discovered), set(owners))
        self.assertEqual(len(owners), len(all_outputs(specs)))
        self.assertIn("coverage/rules-delta.json", report.discovered)
        self.assertIn("demo/pilot-a-bootstrap.json", report.discovered)
        self.assertIn("rules/rule-index.json", report.discovered)
        self.assertIn(
            "web/src/generated/decision-packet.ts",
            report.discovered,
        )

    def test_generated_completeness_rejects_unowned_tracked_artifact(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            coverage = root / "coverage"
            coverage.mkdir()
            (coverage / "owned.json").write_text("{}\n", encoding="utf-8")
            (coverage / "unowned.json").write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            spec = GeneratorSpec(
                id="owned",
                depends_on=(),
                outputs=("coverage/owned.json",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            )
            discovery = GeneratedArtifactDiscoverySpec(
                path_prefixes=("coverage/",),
                path_globs=("rules/*.json",),
                explicit_paths=("platform/module-classifications.json",),
                markdown_statuses=("generated",),
                content_markers=("automatically generated",),
            )

            with self.assertRaisesRegex(
                GeneratedArtifactManifestError,
                "coverage/unowned.json",
            ):
                validate_manifest_completeness(
                    (spec,),
                    discovery,
                    root=root,
                )

    def test_generated_completeness_rejects_missing_registered_output(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            spec = GeneratorSpec(
                id="missing",
                depends_on=(),
                outputs=("coverage/missing.json",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            )
            discovery = GeneratedArtifactDiscoverySpec(
                path_prefixes=("coverage/",),
                path_globs=("rules/*.json",),
                explicit_paths=("platform/module-classifications.json",),
                markdown_statuses=("generated",),
                content_markers=("automatically generated",),
            )

            with self.assertRaisesRegex(
                GeneratedArtifactManifestError,
                "registered generated outputs do not exist",
            ):
                validate_manifest_completeness(
                    (spec,),
                    discovery,
                    root=root,
                )

    def test_generated_completeness_rejects_untracked_registered_output(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            coverage = root / "coverage"
            coverage.mkdir()
            (coverage / "owned.json").write_text("{}\n", encoding="utf-8")
            spec = GeneratorSpec(
                id="owned",
                depends_on=(),
                outputs=("coverage/owned.json",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            )
            discovery = GeneratedArtifactDiscoverySpec(
                path_prefixes=("coverage/",),
                path_globs=("rules/*.json",),
                explicit_paths=("platform/module-classifications.json",),
                markdown_statuses=("generated",),
                content_markers=("automatically generated",),
            )

            with self.assertRaisesRegex(
                GeneratedArtifactManifestError,
                "not tracked by Git",
            ):
                validate_manifest_completeness(
                    (spec,),
                    discovery,
                    root=root,
                )

    def test_generated_completeness_rejects_owner_without_discovery_signal(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            notes = root / "notes"
            notes.mkdir()
            (notes / "owned.json").write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            spec = GeneratorSpec(
                id="owned",
                depends_on=(),
                outputs=("notes/owned.json",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            )
            discovery = GeneratedArtifactDiscoverySpec(
                path_prefixes=("coverage/",),
                path_globs=("rules/*.json",),
                explicit_paths=("platform/module-classifications.json",),
                markdown_statuses=("generated",),
                content_markers=("automatically generated",),
            )

            with self.assertRaisesRegex(
                GeneratedArtifactManifestError,
                "no independent discovery signal",
            ):
                validate_manifest_completeness(
                    (spec,),
                    discovery,
                    root=root,
                )

    def test_separately_generated_protocol_assets_retain_their_sources(self):
        browser = validate_web_types()
        demo = validate_protocol_output(ROOT / "demo")

        self.assertEqual(2, len(browser["outputs"]))
        self.assertEqual("absent", demo["raw_capabilities"])

    def test_generated_manifest_rejects_duplicate_output_and_cycle(self):
        manifest = json.loads(
            (ROOT / "platform" / "generated-artifacts.json").read_text(
                encoding="utf-8"
            )
        )
        duplicate = copy.deepcopy(manifest)
        duplicate["generators"][1]["outputs"].append(
            duplicate["generators"][0]["outputs"][0]
        )
        with self.assertRaisesRegex(
            GeneratedArtifactManifestError, "multiple owners"
        ):
            parse_manifest(duplicate)

        cycle = copy.deepcopy(manifest)
        cycle["generators"][0]["depends_on"] = [
            cycle["generators"][-1]["id"]
        ]
        with self.assertRaisesRegex(GeneratedArtifactManifestError, "cycle"):
            parse_manifest(cycle)

    def test_generated_manifest_rejects_paths_outside_repository(self):
        manifest = json.loads(
            (ROOT / "platform" / "generated-artifacts.json").read_text(
                encoding="utf-8"
            )
        )
        escaped = copy.deepcopy(manifest)
        escaped["generators"][0]["outputs"] = ["../outside.json"]

        with self.assertRaisesRegex(
            GeneratedArtifactManifestError,
            "repository-relative POSIX path",
        ):
            parse_manifest(escaped)

    def test_generated_change_detection_uses_git_visible_content(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Generated Test"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "core.autocrlf", "true"],
                cwd=root,
                check=True,
            )
            output = root / "generated.json"
            output.write_bytes(b'{"ok": true}\n')
            subprocess.run(["git", "add", "generated.json"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fixture"],
                cwd=root,
                check=True,
            )
            output.write_bytes(b'{"ok": true}\r\n')
            spec = GeneratorSpec(
                id="generated",
                depends_on=(),
                outputs=("generated.json",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            )

            self.assertEqual((), changed_generated_outputs((spec,), root=root))

    def test_generated_change_detection_includes_worktree_index_and_untracked(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Generated Test"],
                cwd=root,
                check=True,
            )
            for name in ("worktree.json", "staged.json"):
                (root / name).write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "fixture"],
                cwd=root,
                check=True,
            )
            (root / "worktree.json").write_text("new\n", encoding="utf-8")
            (root / "staged.json").write_text("new\n", encoding="utf-8")
            (root / "untracked.json").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "staged.json"], cwd=root, check=True)
            specs = tuple(
                GeneratorSpec(
                    id=name.removesuffix(".json"),
                    depends_on=(),
                    outputs=(name,),
                    check=("unused.py", "--check"),
                    write=("unused.py", "--write"),
                    write_with_database=None,
                    write_policy="automatic",
                )
                for name in (
                    "worktree.json",
                    "staged.json",
                    "untracked.json",
                )
            )

            self.assertEqual(
                ("staged.json", "untracked.json", "worktree.json"),
                changed_generated_outputs(specs, root=root),
            )

    def test_generated_finalizer_reaches_a_bounded_fixed_point(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            specs = (
                GeneratorSpec(
                    id="first",
                    depends_on=(),
                    outputs=("first.txt",),
                    check=("unused.py", "--check"),
                    write=("unused.py", "--write"),
                    write_with_database=None,
                    write_policy="automatic",
                ),
                GeneratorSpec(
                    id="second",
                    depends_on=("first",),
                    outputs=("second.txt",),
                    check=("unused.py", "--check"),
                    write=("unused.py", "--write"),
                    write_with_database=None,
                    write_policy="automatic",
                ),
            )
            calls: list[str] = []

            def runner(generator_id: str, _command: tuple[str, ...]) -> int:
                calls.append(generator_id)
                if generator_id == "first":
                    (root / "first.txt").write_text("first\n", encoding="utf-8")
                else:
                    value = (root / "first.txt").read_text(encoding="utf-8")
                    (root / "second.txt").write_text(value + "second\n", encoding="utf-8")
                return 0

            result = write_until_stable(
                specs,
                database=None,
                include_manual=False,
                max_passes=3,
                root=root,
                runner=runner,
            )

        self.assertEqual(2, result["passes"])
        self.assertEqual(["first", "second", "first", "second"], calls)
        self.assertEqual(
            ("first.txt", "second.txt"),
            result["changed_by_pass"][0],
        )
        self.assertEqual((), result["changed_by_pass"][1])

    def test_generated_finalizer_rebuilds_database_corpus_only_once(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            database = root / "cards.sqlite3"
            database.write_bytes(b"fixture")
            spec = GeneratorSpec(
                id="corpus",
                depends_on=(),
                outputs=("corpus.txt",),
                check=("unused.py", "--check"),
                write=("unused.py", "--refresh-derived"),
                write_with_database=(
                    "unused.py",
                    "--write",
                    "--db",
                    "{db}",
                ),
                write_policy="database",
            )
            commands: list[tuple[str, ...]] = []

            def runner(_generator_id: str, command: tuple[str, ...]) -> int:
                commands.append(command)
                (root / "corpus.txt").write_text("stable\n", encoding="utf-8")
                return 0

            result = write_until_stable(
                (spec,),
                database=database,
                include_manual=False,
                max_passes=3,
                root=root,
                runner=runner,
            )

        self.assertEqual(2, result["passes"])
        self.assertIn("--write", commands[0])
        self.assertIn(str(database), commands[0])
        self.assertIn("--refresh-derived", commands[1])
        self.assertNotIn(str(database), commands[1])

    def test_stabilization_reruns_only_changed_owners_and_descendants(self):
        specs = (
            GeneratorSpec(
                id="source",
                depends_on=(),
                outputs=("source.txt",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            ),
            GeneratorSpec(
                id="consumer",
                depends_on=("source",),
                outputs=("consumer.txt",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            ),
            GeneratorSpec(
                id="unrelated",
                depends_on=(),
                outputs=("unrelated.txt",),
                check=("unused.py", "--check"),
                write=("unused.py", "--write"),
                write_with_database=None,
                write_policy="automatic",
            ),
        )

        self.assertEqual(
            frozenset({"source", "consumer"}),
            stabilization_ids(specs, ("source.txt",)),
        )

    def test_failed_owner_resume_skips_unrelated_upstream_writers(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            specs = (
                GeneratorSpec(
                    id="expensive-upstream",
                    depends_on=(),
                    outputs=("upstream.txt",),
                    check=("unused.py", "--check"),
                    write=("unused.py", "--write"),
                    write_with_database=None,
                    write_policy="automatic",
                ),
                GeneratorSpec(
                    id="failed-owner",
                    depends_on=("expensive-upstream",),
                    outputs=("failed.txt",),
                    check=("unused.py", "--check"),
                    write=("unused.py", "--refresh-derived"),
                    write_with_database=(
                        "unused.py",
                        "--write",
                        "--db",
                        "{db}",
                    ),
                    write_policy="database",
                ),
            )
            database = root / "cards.sqlite3"
            database.write_bytes(b"fixture")
            calls: list[str] = []

            def runner(generator_id: str, _command: tuple[str, ...]) -> int:
                calls.append(generator_id)
                (root / "failed.txt").write_text("stable\n", encoding="utf-8")
                return 0

            result = write_until_stable(
                specs,
                database=database,
                include_manual=False,
                max_passes=3,
                initial_selected_ids=stabilization_ids(
                    specs,
                    specs[1].outputs,
                ),
                root=root,
                runner=runner,
            )

        self.assertEqual(2, result["passes"])
        self.assertEqual(["failed-owner", "failed-owner"], calls)

    def test_broad_generated_ci_uses_the_canonical_finalizer_only(self):
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = {
            path.name: path.read_text(encoding="utf-8")
            for path in workflow_dir.glob("*.yml")
        }
        for name in ("ci.yml", "main-broad.yml", "nightly.yml"):
            self.assertIn(
                "python scripts/finalize_generated.py --check",
                workflows[name],
            )
        self.assertNotIn(
            "python scripts/finalize_generated.py --check",
            workflows["main-smoke.yml"],
        )
        self.assertIn(
            "python scripts/certification_receipt.py verify-main",
            workflows["main-smoke.yml"],
        )
        combined = "\n".join(workflows.values())
        for spec in load_manifest():
            command = "python " + " ".join(spec.check)
            self.assertNotIn(command, combined)

    def test_generated_pre_push_hook_uses_worktree_python_and_stops_on_changes(self):
        hook = (ROOT / ".githooks" / "pre-push").read_text(
            encoding="utf-8"
        )
        self.assertIn(".venv/bin/python", hook)
        self.assertIn(".venv/Scripts/python.exe", hook)
        self.assertIn("data/scryfall-current.sqlite3", hook)
        self.assertIn('"$ROOT/scripts/test_shards.py" validate', hook)
        self.assertIn("QUORUNE_CLOUD_SOURCE_CHECKPOINT_REASON", hook)
        self.assertIn(
            '"$ROOT/scripts/quick_gate.py" \\\n        --phase pre-corpus --base origin/main',
            hook,
        )
        self.assertIn('if [ -z "$BRANCH" ] || [ "$BRANCH" = "main" ]', hook)
        self.assertIn("--verify-receipt", hook)
        self.assertIn("--write --fail-on-change", hook)
        self.assertIn(
            "existing generated-finalization receipt is current",
            hook,
        )
        self.assertLess(
            hook.index("existing generated-finalization receipt is current"),
            hook.index('run_finalizer --db "$ROOT/data/scryfall-current.sqlite3"'),
        )
        self.assertLess(
            hook.index("--verify-receipt"),
            hook.index("--write --fail-on-change"),
        )
        self.assertNotIn("python scripts/finalize_generated.py", hook)

    def test_finalizer_checks_the_architecture_policy_before_publication(self):
        self.assertIn(
            (
                "architecture-policy",
                ("scripts/validate_architecture.py", "--check"),
            ),
            POST_CHECKS,
        )

        observed: list[tuple[str, tuple[str, ...]]] = []

        def runner(check_id, command):
            observed.append((check_id, tuple(command)))
            return 1 if check_id == "architecture-policy" else 0

        failures = check_all((), runner=runner)
        self.assertIn("architecture-policy", {row[0] for row in observed})
        self.assertEqual(
            ["architecture-policy"],
            [str(row["check"]) for row in failures],
        )

    def test_assembled_finalizer_skips_receipted_owner_checks(self):
        cacheable = GeneratorSpec(
            id="cacheable",
            depends_on=(),
            outputs=("cacheable.txt",),
            check=("cacheable.py", "--check"),
            write=("cacheable.py", "--write"),
            write_with_database=None,
            write_policy="automatic",
            reuse_policy="safe",
        )
        manual = GeneratorSpec(
            id="manual",
            depends_on=("cacheable",),
            outputs=("manual.txt",),
            check=("manual.py", "--check"),
            write=None,
            write_with_database=None,
            write_policy="manual",
            reuse_policy="noncacheable",
        )
        observed: list[str] = []

        failures = check_assembled(
            (cacheable, manual),
            runner=lambda check_id, _command: observed.append(check_id) or 0,
        )

        self.assertEqual((), failures)
        self.assertNotIn("cacheable", observed)
        self.assertIn("manual", observed)
        self.assertTrue(
            {check_id for check_id, _command in POST_CHECKS}
            <= set(observed)
        )

    def test_generated_hook_installer_is_idempotent_and_preserves_foreign_policy(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            tracked = root / ".githooks" / "pre-push"
            tracked.parent.mkdir()
            shutil.copy2(ROOT / ".githooks" / "pre-push", tracked)

            install_hooks(root)
            install_hooks(root)
            check_hooks(root)
            configured = subprocess.check_output(
                ["git", "config", "--local", "--get", "core.hooksPath"],
                cwd=root,
                text=True,
                encoding="utf-8",
            ).strip()
            self.assertEqual(".githooks", configured)

            subprocess.run(
                ["git", "config", "--local", "core.hooksPath", "custom-hooks"],
                cwd=root,
                check=True,
            )
            with self.assertRaisesRegex(
                HookInstallationError, "refusing to replace"
            ):
                install_hooks(root)


if __name__ == "__main__":
    unittest.main()
