from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pytest
import scripts.test_shards as shard_owner

from scripts.pytest_shard_plugin import (
    canonical_collection,
    expected_collection,
)
from scripts.test_shards import (
    canonical_test_ids,
    discovered_modules,
    functional_shards,
    load_manifest,
    load_observed_module_timings,
    load_suite,
    primary_matrix,
    PytestShardRecorder,
    rebalance_primary_shards,
    run_modules,
    suite_modules,
    test_collection_fingerprint,
    TestShardError,
    validate_partition,
    validate_imported_test_case_aliases,
    validate_semantic_program_fixture_identities,
    validate_impact_references,
)


class TestModuleSyntaxFactCacheTests(unittest.TestCase):
    def setUp(self):
        shard_owner._cached_test_module_facts.cache_clear()

    @staticmethod
    def manifest(*names):
        return {'primary_shards': {'functional': list(names)}, 'overlay_suites': {}}

    def test_repeated_checks_reuse_facts_from_current_content(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root/'test_example.py').write_text(
                "SemanticProgram(oracle_id=card.oracle_id, ability_id='fixture')\n", encoding='utf-8')
            parser = shard_owner.ast.parse
            with patch.object(shard_owner.ast, 'parse', wraps=parser) as observed:
                manifest = self.manifest('test_example')
                self.assertEqual(1, validate_partition(manifest, tests_root=root)['test_modules'])
                self.assertEqual(1, validate_semantic_program_fixture_identities(root))
                validate_imported_test_case_aliases(root)
                self.assertEqual(1, validate_partition(manifest, tests_root=root)['test_modules'])
                self.assertEqual(1, observed.call_count)

    def test_same_size_and_time_source_changes_cannot_reuse_an_old_verdict(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            source = root/'test_example.py'
            valid = "SemanticProgram(oracle_id=card.oracle_id, ability_id='fixture')\n"
            invalid = valid.replace('ability_id', 'notability')
            self.assertEqual(len(valid), len(invalid))
            source.write_text(valid, encoding='utf-8')
            manifest = self.manifest('test_example')
            validate_partition(manifest, tests_root=root)
            before = source.stat()
            source.write_text(invalid, encoding='utf-8')
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(before.st_size, source.stat().st_size)
            self.assertEqual(before.st_mtime_ns, source.stat().st_mtime_ns)
            for _ in range(2):
                with self.assertRaisesRegex(TestShardError, 'explicit ability_id'):
                    validate_partition(manifest, tests_root=root)
            source.write_text(valid, encoding='utf-8')
            validate_partition(manifest, tests_root=root)

    def test_import_and_helper_class_changes_are_rechecked(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            helper = root/'test_helper.py'
            owner = root/'test_owner.py'
            helper.write_text('import unittest\nclass HelperTests(unittest.TestCase):\n'
                              '    def test_example(self): pass\n', encoding='utf-8')
            owner.write_text('import test_helper as helpers\n', encoding='utf-8')
            validate_imported_test_case_aliases(root)
            owner.write_text('from tests.test_helper import HelperTests as Borrowed\n', encoding='utf-8')
            with self.assertRaisesRegex(TestShardError, 'test_owner:1:Borrowed'):
                validate_imported_test_case_aliases(root)
            helper.write_text('class HelperTests:\n    def test_example(self): pass\n', encoding='utf-8')
            validate_imported_test_case_aliases(root)
            helper.write_text('from unittest import TestCase\nclass HelperTests(TestCase):\n'
                              '    async def test_example(self): pass\n', encoding='utf-8')
            with self.assertRaisesRegex(TestShardError, 'duplicate collection'):
                validate_imported_test_case_aliases(root)

    def test_new_deleted_and_renamed_files_keep_partition_checks_current(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            first = root/'test_first.py'
            second = root/'test_second.py'
            first.write_text('pass\n', encoding='utf-8')
            validate_partition(self.manifest('test_first'), tests_root=root)
            second.write_text('pass\n', encoding='utf-8')
            with self.assertRaisesRegex(TestShardError, 'missing'):
                validate_partition(self.manifest('test_first'), tests_root=root)
            validate_partition(self.manifest('test_first', 'test_second'), tests_root=root)
            first.unlink()
            with self.assertRaisesRegex(TestShardError, 'unknown'):
                validate_partition(self.manifest('test_first', 'test_second'), tests_root=root)
            validate_partition(self.manifest('test_second'), tests_root=root)
            second.rename(root/'test_renamed.py')
            with self.assertRaises(TestShardError):
                validate_partition(self.manifest('test_second'), tests_root=root)
            validate_partition(self.manifest('test_renamed'), tests_root=root)

    def test_invalid_syntax_encoding_and_missing_current_source_reject_after_success(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            source = root/'test_example.py'
            source.write_text('pass\n', encoding='utf-8')
            manifest = self.manifest('test_example')
            validate_partition(manifest, tests_root=root)
            source.write_text('def broken(\n', encoding='utf-8')
            with self.assertRaisesRegex(TestShardError, 'Unable to inspect'):
                validate_partition(manifest, tests_root=root)
            source.write_bytes(b'\xff')
            with self.assertRaisesRegex(TestShardError, 'Unable to inspect'):
                validate_partition(manifest, tests_root=root)
            source.unlink()
            with self.assertRaisesRegex(TestShardError, 'Unable to inspect'):
                shard_owner._read_test_module_facts(source)
            source.write_text('pass\n', encoding='utf-8')
            validate_partition(manifest, tests_root=root)

    def test_changed_parser_and_walk_policy_cannot_reuse_prior_success(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root/'test_example.py').write_text('pass\n', encoding='utf-8')
            manifest = self.manifest('test_example')
            validate_partition(manifest, tests_root=root)
            for provider in ('parse', 'walk'):
                with self.subTest(provider=provider):
                    with patch.object(shard_owner.ast, provider, side_effect=SyntaxError('changed policy')):
                        with self.assertRaisesRegex(TestShardError, 'Unable to inspect'):
                            validate_partition(manifest, tests_root=root)
            validate_partition(manifest, tests_root=root)

    def test_in_place_analysis_code_change_cannot_reuse_prior_success(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root/'test_example.py').write_text('pass\n', encoding='utf-8')
            manifest = self.manifest('test_example')
            validate_partition(manifest, tests_root=root)
            original = shard_owner._analyze_test_module.__code__
            def reject(*args):
                raise SyntaxError('changed analysis')
            try:
                shard_owner._analyze_test_module.__code__ = reject.__code__
                with self.assertRaisesRegex(TestShardError, 'Unable to inspect'):
                    validate_partition(manifest, tests_root=root)
            finally:
                shard_owner._analyze_test_module.__code__ = original
            validate_partition(manifest, tests_root=root)

    def test_shared_summaries_are_immutable(self):
        with TemporaryDirectory() as raw:
            source = Path(raw)/'test_example.py'
            source.write_text('import unittest\nfrom test_helper import HelperTests\n'
                              'class Example(unittest.TestCase):\n    def test_example(self): pass\n',
                              encoding='utf-8')
            facts = shard_owner._read_test_module_facts(source)
            with self.assertRaises(AttributeError):facts.semantic_program_count = 42
            with self.assertRaises(AttributeError):facts.test_case_classes.add('Injected')
            with self.assertRaises(AttributeError):facts.missing_ability_id_lines.append(9)
            with self.assertRaises(TypeError):facts.imported_names[0] = ('changed', 'name', 'name', 1)
            self.assertEqual(frozenset({'Example'}),
                             shard_owner._read_test_module_facts(source).test_case_classes)

    def test_cache_eviction_is_bounded_and_reintroduced_source_is_parsed(self):
        with TemporaryDirectory() as raw:
            source = Path(raw)/'test_example.py'
            limit = shard_owner._cached_test_module_facts.cache_parameters()['maxsize']
            self.assertIsInstance(limit, int)
            self.assertGreater(limit, 0)
            self.assertLess(limit, 10000)
            parser = shard_owner.ast.parse
            with patch.object(shard_owner.ast, 'parse', wraps=parser) as observed:
                for index in range(limit + 1):
                    source.write_text(f'value = {index}\n', encoding='utf-8')
                    shard_owner._read_test_module_facts(source)
                self.assertLessEqual(shard_owner._cached_test_module_facts.cache_info().currsize, limit)
                before = observed.call_count
                source.write_text('value = 0\n', encoding='utf-8')
                shard_owner._read_test_module_facts(source)
                self.assertEqual(before + 1, observed.call_count)

    def test_separate_directory_roots_keep_separate_import_scopes(self):
        with TemporaryDirectory() as raw:
            left = Path(raw)/'left'
            right = Path(raw)/'right'
            left.mkdir()
            right.mkdir()
            for root in (left, right):
                (root/'test_owner.py').write_text('from test_helper import HelperTests\n', encoding='utf-8')
            (left/'test_helper.py').write_text('import unittest\nclass HelperTests(unittest.TestCase):\n'
                                             '    def test_example(self): pass\n', encoding='utf-8')
            (right/'test_helper.py').write_text('class HelperTests:\n    def test_example(self): pass\n',
                                              encoding='utf-8')
            with self.assertRaisesRegex(TestShardError, 'duplicate collection'):
                validate_imported_test_case_aliases(left)
            validate_imported_test_case_aliases(right)


class TestShardManifestTests(unittest.TestCase):
    def test_impact_references_reject_missing_modules_and_suites_before_collection(self):
        manifest = {'primary_shards': {'functional': ['test_known']},
            'overlay_suites': {'smoke': ['test_known']}}
        policy = {group: [{'id': group+'-rule', 'test_modules': ['test_known'],
            'test_suites': ['smoke']}] for group in ('path_rules', 'symbol_rules')}
        self.assertIsNone(validate_impact_references(manifest, policy))
        for group in policy:
            for field, invalid in (('test_modules', 'test_semantic_choices'), ('test_suites', 'missing-suite')):
                with self.subTest(group=group, field=field):
                    changed = deepcopy(policy); changed[group][0][field] = [invalid]
                    with self.assertRaisesRegex(TestShardError, invalid):
                        validate_impact_references(manifest, changed)

    def test_imported_helper_test_case_aliases_fail_before_matrix_collection(self):
        with TemporaryDirectory() as raw:
            root=Path(raw)
            (root/'test_helper.py').write_text('import unittest\nclass HelperTests(unittest.TestCase):\n    def test_example(self): pass\n',encoding='utf-8')
            (root/'test_owner.py').write_text('from test_helper import HelperTests as Borrowed\n',encoding='utf-8')
            with self.assertRaisesRegex(TestShardError,'duplicate collection'):
                validate_imported_test_case_aliases(root)
            (root/'test_owner.py').write_text('import test_helper as helpers\nclass OwnerTests:\n    method = helpers.HelperTests.test_example\n',encoding='utf-8')
            validate_imported_test_case_aliases(root)
            (root/'test_helper.py').write_text('class HelperTools:\n    def test_example(self): pass\n',encoding='utf-8')
            (root/'test_owner.py').write_text('from test_helper import HelperTools\n',encoding='utf-8')
            validate_imported_test_case_aliases(root)

    def setUp(self):
        self.manifest = load_manifest()

    def test_every_test_module_has_one_primary_shard(self):
        summary = validate_partition(self.manifest)
        self.assertEqual(len(discovered_modules()), summary["test_modules"])

    def test_counter_event_assurance_is_split_across_loadfile_units(self):
        modules = (
            "test_fixed_counter_event_triggers",
            "test_fixed_counter_player_cast_triggers",
            "test_fixed_counter_zone_triggers",
            "test_fixed_entry_return_triggers",
            "test_fixed_source_combat_growth_triggers",
        )
        for module in modules:
            owners = [
                name for name, assigned in self.manifest["primary_shards"].items()
                if module in assigned
            ]
            self.assertEqual(1, len(owners), module)
            self.assertTrue(owners[0].startswith("functional-"), module)
        identifiers = canonical_test_ids(load_suite(modules))
        self.assertGreaterEqual(len(identifiers), 100)
        for module in modules:
            with self.subTest(module=module):
                self.assertTrue(
                    any(
                        identifier.startswith(f"{module}.")
                        for identifier in identifiers
                    )
                )

    def test_primary_matrix_uses_every_authoritative_shard_once(self):
        rows = primary_matrix(self.manifest)["include"]
        self.assertEqual(
            list(self.manifest["execution_order"]),
            [row["shard"] for row in rows],
        )
        self.assertIn("generated-validation", [row["shard"] for row in rows])
        self.assertEqual("generated-validation", rows[0]["shard"])
        self.assertEqual(set(self.manifest["primary_shards"]), {row["shard"] for row in rows})
        self.assertEqual(
            [
                name
                for name in self.manifest["execution_order"]
                if name != "generated-validation"
            ],
            list(functional_shards(self.manifest)),
        )

    def test_execution_order_is_a_strict_permutation(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "test-shards.json"
            for mutation, message in (
                (lambda value: value["execution_order"].pop(), "every primary"),
                (
                    lambda value: value["execution_order"].append(
                        value["execution_order"][0]
                    ),
                    "duplicates",
                ),
                (
                    lambda value: value["execution_order"].__setitem__(0, "unknown"),
                    "every primary",
                ),
            ):
                mutated = deepcopy(self.manifest)
                mutation(mutated)
                path.write_text(json.dumps(mutated), encoding="utf-8")
                with self.subTest(message=message):
                    with self.assertRaisesRegex(TestShardError, message):
                        load_manifest(path)

    def test_duplicate_primary_assignment_fails_closed(self):
        first, second = functional_shards(self.manifest)[:2]
        mutated = deepcopy(self.manifest)
        module = mutated["primary_shards"][first][0]
        mutated["primary_shards"][second].append(module)
        with self.assertRaisesRegex(TestShardError, "duplicates"):
            validate_partition(mutated)

    def test_missing_primary_assignment_fails_closed(self):
        first = functional_shards(self.manifest)[0]
        mutated = deepcopy(self.manifest)
        mutated["primary_shards"][first].pop()
        with self.assertRaisesRegex(TestShardError, "missing"):
            validate_partition(mutated)

    def test_runtime_oracle_program_fixtures_require_unique_ability_ids(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "test_fixture.py"
            path.write_text(
                "SemanticProgram(oracle_id=source.oracle_id, "
                "ability_id='test:fixture')\n",
                encoding="utf-8",
            )
            self.assertEqual(
                1,
                validate_semantic_program_fixture_identities(root),
            )
            path.write_text(
                "SemanticProgram(oracle_id=source.oracle_id)\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                TestShardError,
                "runtime Oracle ID require an explicit ability_id",
            ):
                validate_semantic_program_fixture_identities(root)

    def test_observed_timing_loader_requires_one_successful_ubuntu_result(self):
        document = {
            "schema_version": 3,
            "type": "pytest-xdist-shard-result",
            "platform": "ubuntu",
            "suite": "fixture",
            "modules": ["test_first", "test_second"],
            "successful": True,
            "backend": "pytest-xdist",
            "workers": 4,
            "distribution": "loadfile",
            "module_timings": [
                {
                    "module": "test_first",
                    "worker_elapsed_seconds": 2.5,
                },
                {
                    "module": "test_second",
                    "worker_elapsed_seconds": 1.25,
                },
            ],
        }
        with TemporaryDirectory() as raw:
            path = Path(raw) / "fixture.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(
                {"test_first": 2.5, "test_second": 1.25},
                load_observed_module_timings(Path(raw)),
            )
            document["workers"] = 3
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(TestShardError, "execution shape"):
                load_observed_module_timings(Path(raw))

    def test_duration_rebalance_preserves_semantic_impact_suites(self):
        original_functional = functional_shards(self.manifest)
        semantic_modules = {
            name: suite_modules(self.manifest, name)
            for name in (
                "compiler-cardprogram",
                "targets-choices-continuations",
            )
        }
        modules = sorted(
            module
            for name in original_functional
            for module in self.manifest["primary_shards"][name]
        )
        timings = {
            module: float(1 + index % 23)
            for index, module in enumerate(modules)
        }
        balanced, summary = rebalance_primary_shards(
            self.manifest,
            timings,
        )
        validate_partition(balanced)
        self.assertEqual("generated-validation", balanced["execution_order"][0])
        self.assertEqual(len(original_functional), summary["functional_shards"])
        self.assertTrue(
            all(
                name.startswith("functional-")
                for name in functional_shards(balanced)
            )
        )
        for name, expected in semantic_modules.items():
            with self.subTest(name=name):
                self.assertEqual(
                    expected,
                    suite_modules(balanced, name),
                )
        makespans = [
            row["makespan_seconds"]
            for row in summary["predicted_shards"].values()
        ]
        self.assertLessEqual(max(makespans) - min(makespans), 23.0)

    def test_overlay_suites_are_explicit_and_known(self):
        windows = suite_modules(self.manifest, "windows-compat")
        self.assertIn("test_server_app", windows)
        self.assertIn("test_game_record_v3", windows)
        merge_core = suite_modules(self.manifest, "merge-core")
        self.assertIn("test_card_program_trust", merge_core)
        self.assertIn("test_permissions_projection", merge_core)
        self.assertIn("test_high_risk_interaction_assurance", merge_core)

    def test_unknown_suite_fails_closed(self):
        with self.assertRaisesRegex(TestShardError, "Unknown test suite"):
            suite_modules(self.manifest, "not-a-suite")

    def test_unittest_collection_has_one_stable_canonical_fingerprint(self):
        modules = suite_modules(self.manifest, "main-smoke")
        first = canonical_test_ids(load_suite(modules))
        second = canonical_test_ids(load_suite(tuple(reversed(modules))))
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(
            test_collection_fingerprint(first),
            test_collection_fingerprint(second),
        )
        self.assertNotEqual(
            test_collection_fingerprint(first),
            test_collection_fingerprint((*first[:-1], first[-1] + "-changed")),
        )

    def test_parallel_worker_configuration_fails_closed(self):
        with self.assertRaisesRegex(TestShardError, "at least two workers"):
            run_modules(
                ("test_rules_primitives",),
                backend="pytest-xdist",
                workers=1,
            )
        with self.assertRaisesRegex(TestShardError, "exactly one worker"):
            run_modules(
                ("test_rules_primitives",),
                backend="unittest",
                workers=2,
            )

    def test_pytest_collection_adapter_rejects_non_unittest_and_duplicates(self):
        def item(identifier: str):
            return SimpleNamespace(
                nodeid=identifier,
                _testcase=SimpleNamespace(id=lambda: identifier),
            )

        self.assertEqual(("a", "b"), canonical_collection((item("b"), item("a"))))
        with self.assertRaisesRegex(pytest.UsageError, "duplicate"):
            canonical_collection((item("a"), item("a")))
        with self.assertRaisesRegex(pytest.UsageError, "only accept unittest"):
            canonical_collection((SimpleNamespace(nodeid="free_function"),))

    def test_expected_parallel_collection_fails_closed_for_malformed_data(self):
        with TemporaryDirectory() as raw:
            path = Path(raw) / "expected.json"
            path.write_text(json.dumps(["b", "a"]), encoding="utf-8")
            with self.assertRaisesRegex(pytest.UsageError, "canonically sorted"):
                expected_collection(path)
            path.write_text(json.dumps(["a", "a"]), encoding="utf-8")
            with self.assertRaisesRegex(pytest.UsageError, "duplicate"):
                expected_collection(path)

    def test_parallel_result_recorder_reports_observed_module_time(self):
        recorder = PytestShardRecorder()
        recorder.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid="tests/test_sample.py::Case::test_ok",
                duration=1.25,
                skipped=False,
                failed=False,
                passed=True,
                when="call",
            )
        )
        recorder.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid="tests/test_sample.py::Case::test_bad",
                duration=0.5,
                skipped=False,
                failed=True,
                passed=False,
                when="call",
            )
        )
        self.assertEqual(2, len(recorder.seen_items))
        self.assertEqual(1, recorder.failures)
        self.assertEqual(
            {"tests.test_sample.Case.test_bad"},
            recorder.failed_test_ids,
        )
        self.assertEqual(set(), recorder.error_test_ids)
        self.assertEqual(
            [{"module": "test_sample", "worker_elapsed_seconds": 1.75}],
            recorder.module_timings(),
        )


if __name__ == "__main__":
    unittest.main()
