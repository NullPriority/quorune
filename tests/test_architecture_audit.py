from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from scripts.update_architecture_audit import (
    GENERATED_VERIFIED_SENTINEL,
    ROOT,
    _check_outputs,
    _discover_test_case_count,
    build_report,
)


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class ArchitectureTestImportInventoryTests(unittest.TestCase):
    def test_shared_import_inventory_keeps_matching_and_rereads_after_edits(self):
        from scripts.architecture_observability import _test_import_inventory, _tests_for_modules

        with TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "tests"
            tests.mkdir()
            first = tests / "test_first.py"
            first.write_text("import quorune.damage.child\nfrom quorune.draw import owner\n", encoding="utf-8")
            second = tests / "test_second.py"
            second.write_text("import quorune.damage_extra\ndef test_nested():\n    import quorune.damage\n", encoding="utf-8")
            (tests / "test_malformed.py").write_text("def (\n", encoding="utf-8")
            inventory = _test_import_inventory(root)
            with mock.patch("scripts.architecture_observability.ast.parse", side_effect=AssertionError("reparsed inventory")):
                self.assertEqual(["test_first", "test_second"], _tests_for_modules(root, {"quorune.damage"}, inventory=inventory))
                self.assertEqual(["test_first"], _tests_for_modules(root, {"quorune.draw"}, inventory=inventory))
                self.assertEqual([], _tests_for_modules(root, {"quorune.damage.child.other"}, inventory=inventory))
            first.write_text("import quorune.life\n", encoding="utf-8")
            second.unlink()
            self.assertEqual([], _tests_for_modules(root, {"quorune.damage"}))
            self.assertEqual(["test_first"], _tests_for_modules(root, {"quorune.life"}))


class ArchitectureSourceReadinessTests(unittest.TestCase):
    def test_readiness_rejects_same_metadata_edits_and_preserves_canonical_blobs(self):
        from scripts.source_tree_fingerprint import (
            SOURCE_TREE_FINGERPRINT_ALGORITHM,
            tracked_worktree_source_fingerprint,
        )
        from scripts.update_architecture_audit import _source_readiness_errors

        with TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            source = root / "owner.py"
            source.write_bytes(b"VALUE = 1\n")
            report_path = root / "coverage/architecture-audit.json"
            report_path.parent.mkdir()
            report_path.write_text("{}", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "--", "owner.py", "coverage/architecture-audit.json"], check=True)
            observed = {"fingerprint_algorithm": SOURCE_TREE_FINGERPRINT_ALGORITHM,
                "fingerprint": tracked_worktree_source_fingerprint(root)}
            report = {"coordinates": {"evaluated_source_tree": observed}}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            self.assertEqual([], _source_readiness_errors(root))
            metadata = source.stat()
            source.write_bytes(b"VALUE = 2\n")
            os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            self.assertIn("stale", _source_readiness_errors(root)[0])
            source.write_bytes(b"VALUE = 1\n")
            self.assertEqual([], _source_readiness_errors(root))
            observed["fingerprint_algorithm"] = "unsupported"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            self.assertIn("algorithm", _source_readiness_errors(root)[0])
            report_path.write_text("[]", encoding="utf-8")
            self.assertIn("malformed", _source_readiness_errors(root)[0])
            report_path.unlink()
            self.assertIn("missing", _source_readiness_errors(root)[0])

    def test_readiness_mode_does_not_build_or_write_reports(self):
        from scripts import update_architecture_audit as owner

        with mock.patch("sys.argv", ["audit", "--check-source-readiness"]), \
             mock.patch.object(owner, "_source_readiness_errors", return_value=[]) as check, \
             mock.patch.object(owner, "build_report", side_effect=AssertionError("rebuilt report")), \
             mock.patch.object(owner, "_write_outputs", side_effect=AssertionError("wrote report")):
            self.assertEqual(0, owner.main())
            check.assert_called_once_with(owner.ROOT)


class ArchitectureAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_report()

    def test_generated_outputs_are_current(self):
        self.assertEqual(_check_outputs(self.report), [])

    def test_report_reconciles_measured_architecture_and_tests(self):
        architecture = self.report["architecture"]
        production = architecture["production"]
        engine = architecture["engine"]
        tests = self.report["tests"]

        engine_lines = len(
            (ROOT / "quorune" / "engine.py")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertEqual(engine["physical_lines"], engine_lines)
        self.assertEqual(
            production["oversized_module_count"],
            len(production["oversized_modules"]),
        )
        self.assertEqual(
            production["oversized_function_and_method_count"],
            len(production["oversized_functions_and_methods"]),
        )
        self.assertEqual(
            architecture["direct_game_state_write_heuristic"]["count"],
            len(architecture["direct_game_state_write_heuristic"]["locations"]),
        )
        handlers = architecture["semantic_handlers"]
        self.assertEqual(
            handlers["registered_handler_count"],
            len(handlers["registered_operations"]),
        )
        self.assertTrue(
            {
                "become_monarch",
                "draw",
                "draw_each_player",
                "place_counter_batch",
                "place_counters",
                "place_player_counters",
                "reanimate_attached_creature_aura",
                "tap",
                "untap",
                "untap_all_creatures",
            }.issubset(handlers["registered_operations"])
        )
        self.assertEqual(
            [], handlers["registered_operations_still_in_legacy_dispatch"]
        )
        self.assertEqual(0, handlers["legacy_apply_effect_branch_count"])
        self.assertEqual(3, handlers["engine_string_dispatch_branch_count"])
        self.assertEqual(
            handlers["registered_runtime_handler_count"],
            len(handlers["runtime_handlers"]),
        )
        self.assertIn(
            "continuous.basic_land_type.add_all_lands.v1",
            {
                handler["handler_id"]
                for handler in handlers["runtime_handlers"]
            },
        )
        self.assertIn(
            "replacement.draw.dredge.v1",
            {
                handler["handler_id"]
                for handler in handlers["runtime_handlers"]
            },
        )
        self.assertTrue(
            {
                "combat.block.self-counter-prohibition.v1",
                "replacement.zone.riot-entry-choice.v1",
                "replacement.zone.self-entry-counter.v1",
                "replacement.counter.quantity.v2",
            }.issubset(
                {
                    handler["handler_id"]
                    for handler in handlers["runtime_handlers"]
                }
            )
        )
        self.assertIn(
            "generic.fixed-counter-placement-batch.v1",
            {
                handler["handler_id"]
                for handler in handlers["handlers"]
            },
        )
        self.assertIn(
            "ability.static.flash.v1",
            {
                handler["handler_id"]
                for handler in handlers["runtime_handlers"]
            },
        )
        self.assertTrue(tests["python"]["reconciles"])
        self.assertEqual(
            tests["python"]["discovered_total"],
            tests["python"]["conventional_ast_cases"]
            + tests["python"]["generated_rule_conformance_cases"],
        )

    def test_architecture_test_discovery_fails_closed_on_loader_errors(self):
        class BrokenLoader:
            errors = ["broken test import"]

            def discover(self, *_args, **_kwargs):
                return unittest.TestSuite()

        with mock.patch(
            "scripts.update_architecture_audit.unittest.TestLoader",
            return_value=BrokenLoader(),
        ):
            with self.assertRaisesRegex(RuntimeError, "broken test import"):
                _discover_test_case_count()

    def test_report_tracks_pinned_compiler_semantics_and_document_drift(self):
        compiler = self.report["compiler"]
        oracle = _json("coverage/oracle-coverage.json")
        rules = _json("rules/manifest.json")
        semantics = self.report["semantic_packs_and_overrides"]
        documents = self.report["documentation"]

        self.assertEqual(compiler["compiler_version"], oracle["compiler_version"])
        self.assertEqual(
            self.report["rules"]["comprehensive_rules"]["source_sha256"],
            rules["source_sha256"],
        )
        self.assertEqual(
            semantics["program_entries"],
            semantics["unique_program_keys"] + semantics["duplicate_key_count"],
        )
        self.assertEqual(
            semantics["configured_card_specific_operations_not_observed"], []
        )
        self.assertEqual(
            documents["required_count"],
            documents["present_count"] + documents["missing_count"],
        )
        self.assertEqual(
            documents["metadata_complete_count"], documents["present_count"]
        )
        self.assertEqual(documents["required_count"], documents["present_count"])
        self.assertEqual(0, documents["missing_count"])
        generated_documents = [
            row
            for row in documents["documents"]
            if row["metadata"].get("status") == "generated"
        ]
        self.assertTrue(generated_documents)
        self.assertTrue(
            all(
                row["metadata"]["verified"]
                == GENERATED_VERIFIED_SENTINEL
                for row in generated_documents
            )
        )
        self.assertTrue(documents["policy"]["metadata_enforced"])
        self.assertTrue(documents["policy"]["internal_links_enforced"])
        self.assertTrue(documents["policy"]["stale_claims_enforced"])
        self.assertTrue(documents["policy"]["adr_system_enforced"])
        identity_flow = self.report["architecture"]["card_identity_flow"]
        self.assertEqual(3, self.report["schema_version"])
        self.assertEqual(
            0, identity_flow["counts"]["prohibited_identity_dispatch_count"]
        )
        self.assertEqual(
            {
                "identity_as_data",
                "implementation_map_lookup",
                "static_identity_comparison",
                "static_match_dispatch",
                "static_membership",
            },
            set(identity_flow["vocabulary"]["sink_kinds"]),
        )
        self.assertIn(
            "prohibited_identity_dispatch",
            identity_flow["vocabulary"]["classification_vocabulary"],
        )
        self.assertFalse(any(identity_flow["external_dependencies"].values()))
        debt_trend = self.report["architecture"]["debt_trend"]
        self.assertIsNotNone(debt_trend)
        self.assertIn(
            "prohibited_identity_dispatch_count", debt_trend["dimensions"]
        )
        self.assertNotIn("printed_name_literals", debt_trend["dimensions"])


if __name__ == "__main__":
    unittest.main()
