from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.util import stable_json
from scripts import build_test_database as database_builder
from scripts.compact_ci_report import (
    COMPACT_CI_REPORT_PATH,
    build_dependency_report,
    report_markdown,
    store_dependency_artifact,
)
from scripts import update_compact_ci_dependencies as compact_writer
from scripts.finalize_generated import check_all, write_until_stable
from scripts.generated_artifacts import load_manifest
from scripts.generated_owner_cache import GeneratedOwnerCacheError


def card(name: str, oracle_id: str, *, text: str = "") -> dict[str, object]:
    return {
        "oracle_id": oracle_id,
        "name": name,
        "mana_cost": "{1}",
        "cmc": 1.0,
        "type_line": "Creature — Test",
        "oracle_text": text,
        "power": "1",
        "toughness": "1",
        "loyalty": None,
        "defense": None,
        "colors": [],
        "color_identity": [],
        "keywords": [],
        "produced_mana": [],
        "layout": "normal",
        "released_at": "2026-01-01",
        "legalities": {"commander": "legal"},
    }


class TemporaryDependencyRepository:
    def __init__(
        self,
        root: Path,
        *,
        cards: list[dict[str, object]],
        source: str,
        declarations: list[dict[str, object]] | None = None,
        exclusions: list[dict[str, str]] | None = None,
        extra_fixtures: dict[str, list[dict[str, object]]] | None = None,
    ):
        self.root = root
        (root / "tests/fixtures").mkdir(parents=True)
        (root / "platform").mkdir()
        (root / "examples").mkdir()
        (root / "tests/test_fixture.py").write_text(source, encoding="utf-8")
        fixtures = {"tests/fixtures/cards.json": cards}
        fixtures.update(extra_fixtures or {})
        for relative, fixture_cards in fixtures.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "fixture_kind": "public_test_card_data",
                        "cards": fixture_cards,
                        "rulings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        (root / "tests/fixtures/compact-ci-fixtures.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "fixture_kind": "compact_ci_card_database_inputs",
                    "fixtures": ["tests/fixtures/cards.json"],
                    "dynamic_requirements": declarations or [],
                    "full_database_only": exclusions or [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "platform/test-shards.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "execution_order": ["generated-validation"],
                    "primary_shards": {"generated-validation": ["test_fixture"]},
                    "overlay_suites": {"windows-compat": ["test_fixture"]},
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def report(self) -> dict[str, object]:
        return build_dependency_report(
            root=self.root,
            source_fingerprint="0" * 64,
        )


class CompactDependencyArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name).resolve()
        self.root = folder / "repository"
        self.root.mkdir()
        self.artifact = folder / "artifact"
        TemporaryDependencyRepository(
            self.root,
            cards=[card("Fixture Card", "00000000-0000-4000-8000-000000000001")],
            source='db.lookup("Fixture Card")\n',
        )

        manifest = json.loads((ROOT / "platform/generated-artifacts.json").read_bytes())
        by_id = {row["id"]: row for row in manifest["generators"]}
        compact = by_id["compact-ci-card-dependencies"]
        upstream = by_id["ci-escape-report"]
        self.upstream = self.root / "coverage/upstream.json"
        compact["depends_on"] = [upstream["id"]]
        upstream["outputs"] = ["coverage/upstream.json"]
        manifest["generators"] = [upstream, compact]
        (self.root / "platform/generated-artifacts.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )
        self.source = self.root / "README.md"
        self.source.write_text("Original tracked source\n", encoding="utf-8")
        (self.root / ".gitattributes").write_text(
            "*.json text eol=lf\n*.md text eol=lf\n", encoding="utf-8",
        )
        self.json_output = self.root / COMPACT_CI_REPORT_PATH
        self.markdown_output = self.json_output.with_suffix(".md")
        self.json_output.parent.mkdir()
        for path in (self.json_output, self.markdown_output, self.upstream):
            path.write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "core.safecrlf=false", "add", "."],
            cwd=self.root, check=True,
        )

        self.report = build_dependency_report(root=self.root)
        self.assertTrue(self.report["closed"])
        self.json_output.write_text(stable_json(self.report) + "\n", encoding="utf-8", newline="\n")
        self.markdown_output.write_text(report_markdown(self.report), encoding="utf-8", newline="\n")

    def cli(self, *arguments):
        argv = ["build_test_database.py", "validate-ci-dependencies", *arguments]
        output = StringIO()
        with (
            mock.patch.object(database_builder, "ROOT", self.root),
            mock.patch.object(sys, "argv", argv),
            redirect_stdout(output),
        ):
            self.assertEqual(0, database_builder.main())
        return json.loads(output.getvalue())

    def test_compact_artifact_cli_checks_contents_and_preserves_consumer_line_endings(self):
        self.assertEqual(
            self.cli(), self.cli("--emit-artifact", str(self.artifact)),
        )
        converted = {}
        for path in (self.json_output, self.markdown_output):
            converted[path] = path.read_bytes().replace(b"\n", b"\r\n")
            path.write_bytes(converted[path])
        with mock.patch.object(
            database_builder, "build_dependency_report",
            side_effect=AssertionError("A verified artifact must avoid the full audit"),
        ):
            summary = self.cli("--verify-artifact", str(self.artifact))
        self.assertTrue(summary["closed"])
        self.assertEqual(1, summary["fixture_count"])
        self.assertEqual(converted, {path: path.read_bytes() for path in converted})

    def test_pre_push_reuses_only_exact_compact_bundle_evidence(self):
        from scripts.compact_ci_report import (
            current_dependency_report_receipt,
            remember_bundled_dependency_artifact,
        )

        complete = current_dependency_report_receipt(root=self.root)
        remember_bundled_dependency_artifact(root=self.root, input_fingerprint="0" * 64)
        cache = self.root / "local/generated-owner-cache/compact-ci-card-dependencies"
        self.assertFalse(cache.exists())
        with mock.patch.object(
            database_builder, "build_dependency_report", wraps=build_dependency_report,
        ) as audit:
            self.assertTrue(self.cli("--reuse-local-artifact")["closed"])
            audit.assert_called_once_with(root=self.root)

        remember_bundled_dependency_artifact(root=self.root, input_fingerprint=complete.input_fingerprint)
        with mock.patch.object(
            database_builder, "build_dependency_report",
            side_effect=AssertionError("Exact receipt should perform verification only"),
        ):
            self.assertTrue(self.cli("--reuse-local-artifact")["closed"])

        artifact = cache / complete.input_fingerprint
        stored = artifact / COMPACT_CI_REPORT_PATH
        stored.write_bytes(stored.read_bytes() + b"\n")
        with mock.patch.object(
            database_builder, "build_dependency_report", wraps=build_dependency_report,
        ) as audit:
            self.assertTrue(self.cli("--reuse-local-artifact")["closed"])
            audit.assert_called_once_with(root=self.root)

    def test_stale_local_report_falls_back_before_cache_key_resolution(self):
        from scripts import compact_ci_report

        current = compact_ci_report.current_dependency_report_receipt(root=self.root)
        compact_ci_report.remember_bundled_dependency_artifact(
            root=self.root, input_fingerprint=current.input_fingerprint,
        )
        self.source.write_text("Changed tracked source\n", encoding="utf-8")
        with (
            mock.patch.object(
                compact_ci_report, "_dependency_artifact_owner",
                side_effect=AssertionError("Stale report reached owner-key resolution"),
            ),
            mock.patch.object(
                database_builder, "build_dependency_report", wraps=build_dependency_report,
            ) as audit,
        ):
            self.assertTrue(self.cli("--reuse-local-artifact")["closed"])
            audit.assert_called_once_with(root=self.root)

    def test_compact_artifact_rejects_changed_declared_inputs_and_upstream_outputs(self):
        self.cli("--emit-artifact", str(self.artifact))
        for path in (self.root / "tests/test_fixture.py", self.upstream):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                try:
                    with self.assertRaisesRegex(GeneratedOwnerCacheError, "stale"):
                        self.cli("--verify-artifact", str(self.artifact))
                finally:
                    path.write_bytes(original)

    def test_compact_artifact_rejects_corrupt_output_and_missing_files(self):
        self.cli("--emit-artifact", str(self.artifact))
        archived = self.artifact / COMPACT_CI_REPORT_PATH
        archived.write_bytes(archived.read_bytes() + b"\n")
        with self.assertRaisesRegex(GeneratedOwnerCacheError, "corrupt"):
            self.cli("--verify-artifact", str(self.artifact))
        receipt_path = self.artifact / "_owner_receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        output = receipt["outputs"][COMPACT_CI_REPORT_PATH.as_posix()]
        output["raw_sha256"] = sha256(archived.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(GeneratedOwnerCacheError, "corrupt"):
            self.cli("--verify-artifact", str(self.artifact))
        archived.unlink()
        with self.assertRaises(FileNotFoundError):
            self.cli("--verify-artifact", str(self.artifact))
        (self.artifact / "_owner_receipt.json").unlink()
        with self.assertRaisesRegex(GeneratedOwnerCacheError, "missing or malformed"):
            self.cli("--verify-artifact", str(self.artifact))

    def test_compact_artifact_requires_current_source_beyond_the_owner_input_groups(self):
        self.cli("--emit-artifact", str(self.artifact))
        self.source.write_text("Changed tracked source\n", encoding="utf-8")
        with self.assertRaisesRegex(GeneratedOwnerCacheError, "current source tree"):
            self.cli("--verify-artifact", str(self.artifact))
        with self.assertRaisesRegex(GeneratedOwnerCacheError, "current source tree"):
            store_dependency_artifact(
                self.report, artifact_dir=self.artifact, root=self.root,
            )

    def test_compact_artifact_producer_rejects_stale_generated_report(self):
        self.markdown_output.write_text("Stale report\n", encoding="utf-8")
        with self.assertRaisesRegex(GeneratedOwnerCacheError, "open or stale"):
            self.cli("--emit-artifact", str(self.artifact))
        self.assertFalse(self.artifact.exists())


    def run_finalization(self, change_second_pass=None):
        manifest = self.root / "platform/generated-artifacts.json"
        specs = load_manifest(manifest, root=self.root)
        calls = []

        def runner(owner, command):
            mode = command[-1]
            calls.append((owner, mode))
            if owner == "ci-escape-report" and mode == "--write":
                count = calls.count((owner, mode))
                if count == 1:
                    self.upstream.write_text('{"revision": 1}\n', encoding="utf-8")
                elif count == 2 and change_second_pass is not None:
                    change_second_pass()
                return 0
            if owner == "compact-ci-card-dependencies":
                with (
                    mock.patch.object(compact_writer, "ROOT", self.root),
                    mock.patch.object(compact_writer, "JSON_OUTPUT", self.json_output),
                    mock.patch.object(compact_writer, "MARKDOWN_OUTPUT", self.markdown_output),
                    mock.patch.object(sys, "argv", ["update_compact_ci_dependencies.py", mode]),
                    redirect_stdout(StringIO()),
                ):
                    return compact_writer.main()
            return 0

        with redirect_stdout(StringIO()):
            result = write_until_stable(
                specs, database=None, include_manual=False,
                max_passes=3, root=self.root, runner=runner,
            )
            self.assertEqual((), check_all(specs, runner=runner))
        self.assertEqual(1, calls.count(("compact-ci-card-dependencies", "--check")))
        return result, calls.count(("compact-ci-card-dependencies", "--write"))

    def test_generated_finalization_reuses_a_stable_compact_write_with_full_freshness_check(self):
        self.markdown_output.write_text("Stale report\n", encoding="utf-8")
        result, writes = self.run_finalization()
        self.assertEqual(2, result["passes"])
        self.assertEqual(1, writes)
        self.assertNotIn("compact-ci-card-dependencies", result["executed_by_pass"][1])

        next_run, next_writes = self.run_finalization()
        self.assertEqual(1, next_run["passes"])
        self.assertEqual(1, next_writes)

    def test_generated_finalization_reruns_compact_write_after_upstream_changes(self):
        def mutate():
            self.upstream.write_text('{"revision": 2}\n', encoding="utf-8")

        result, writes = self.run_finalization(mutate)
        self.assertEqual(3, result["passes"])
        self.assertEqual(2, writes)

    def test_generated_finalization_reruns_compact_write_after_report_corruption(self):
        def mutate():
            self.markdown_output.write_text("Corrupt report\n", encoding="utf-8")

        result, writes = self.run_finalization(mutate)
        self.assertEqual(2, result["passes"])
        self.assertEqual(2, writes)
        self.assertEqual(report_markdown(self.report), self.markdown_output.read_text(encoding="utf-8"))

    def test_generated_finalization_reruns_compact_write_after_other_tracked_source_changes(self):
        def mutate():
            self.source.write_text("Different tracked source\n", encoding="utf-8")

        result, writes = self.run_finalization(mutate)
        self.assertEqual(3, result["passes"])
        self.assertEqual(2, writes)
        updated = json.loads(self.json_output.read_bytes())
        self.assertNotEqual(self.report["source_tree_fingerprint"], updated["source_tree_fingerprint"])


class DependencySyntaxScopeTests(unittest.TestCase):
    def sources(self, root):
        tests = root / 'tests'
        tests.mkdir()
        helper = tests / 'support.py'
        helper.write_text('def pick(db, name="Default Card"):\n    return db.lookup(name)\n', encoding='utf-8')
        (tests / 'test_first.py').write_text('from support import pick\ndef test_case(db):\n    pick(db, "First Card")\n', encoding='utf-8')
        (tests / 'test_second.py').write_text('import support as alias\ndef test_case(db):\n    alias.pick(db, name="Second Card")\n', encoding='utf-8')
        return helper

    def test_one_build_shares_syntax_but_keeps_wrapper_and_provenance_contexts(self):
        import ast
        from scripts import compact_ci_dependencies as owner

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.sources(root)
            scope = owner.DependencySourceScope()
            parse = ast.parse
            inventory = owner._function_inventory
            with (
                mock.patch('scripts.source_syntax.ast.parse', wraps=parse) as parser,
                mock.patch.object(owner, '_function_inventory', wraps=inventory) as functions,
                mock.patch.object(owner, '_wrapper_inventory', wraps=owner._wrapper_inventory) as wrappers,
            ):
                first, first_dynamic, first_errors = owner.discover_module_requirements('test_first', root=root, sources=scope)
                second, second_dynamic, second_errors = owner.discover_module_requirements('test_second', root=root, sources=scope)
                self.assertEqual(3, parser.call_count)
                self.assertEqual(3, functions.call_count)
                self.assertEqual(3, wrappers.call_count)
            self.assertEqual([], list(first_errors + second_errors))
            self.assertEqual([], list(first_dynamic + second_dynamic))
            self.assertEqual({'First Card'}, {row.value for row in first})
            self.assertEqual({'Second Card'}, {row.value for row in second})
            self.assertEqual({'test_first'}, {row.provenance.module for row in first})
            self.assertEqual({'test_second'}, {row.provenance.module for row in second})

    def test_new_scope_rereads_helper_and_preserves_malformed_missing_and_unreadable_errors(self):
        from scripts import compact_ci_dependencies as owner

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            helper = self.sources(root)
            first, _, errors = owner.discover_module_requirements('test_first', root=root)
            self.assertFalse(errors)
            self.assertEqual({'First Card'}, {row.value for row in first})
            helper.write_text('def pick(db, name="Default Card"):\n    return db.lookup("Changed Card")\n', encoding='utf-8')
            changed, _, errors = owner.discover_module_requirements('test_first', root=root)
            self.assertFalse(errors)
            self.assertEqual({'Changed Card'}, {row.value for row in changed})
            helper.write_text('def (\n', encoding='utf-8')
            self.assertIn('Cannot parse', owner.discover_module_requirements('test_first', root=root)[2][0])
            helper.write_text('def pick(db, name):\n    return db.lookup(name)\n', encoding='utf-8')
            with mock.patch.object(Path, 'read_text', side_effect=PermissionError('unreadable helper')):
                self.assertIn('unreadable helper', owner.discover_module_requirements('test_first', root=root)[2][0])
            (root / 'tests/test_first.py').unlink()
            self.assertIn('Missing test source', owner.discover_module_requirements('test_first', root=root)[2][0])

    def test_shared_helper_keeps_each_consumers_inherited_database_mode(self):
        from scripts import compact_ci_dependencies as owner

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); tests = root / 'tests'; tests.mkdir()
            (tests / 'compact_base.py').write_text(
                'class Fixture:\n @classmethod\n def setUpClass(cls):\n  cls.db=CardDatabase(DB_PATH)\n', encoding='utf-8')
            local = tests / 'local_base.py'
            local.write_text('class Fixture:\n @classmethod\n def setUpClass(cls):\n'
                '  build_fixture_database([], path)\n  cls.db=CardDatabase(path)\n', encoding='utf-8')
            (tests / 'shared.py').write_text(
                'class Shared(Fixture):\n def pick(self,name):\n  return self.db.lookup(name)\n', encoding='utf-8')
            for module, base, name in [('test_first', 'compact_base', 'First Card'),
                    ('test_second', 'local_base', 'Second Card')]:
                (tests / (module + '.py')).write_text(f'from {base} import Fixture\nfrom shared import Shared\n'
                    f'class Use(Shared):\n def test_case(self):\n  self.pick("{name}")\n', encoding='utf-8')
            scope = owner.DependencySourceScope()
            first, dynamic, errors = owner.discover_module_requirements('test_first', root=root, sources=scope)
            self.assertFalse(dynamic + errors); self.assertEqual({'First Card'}, {r.value for r in first})
            second, dynamic, errors = owner.discover_module_requirements('test_second', root=root, sources=scope)
            self.assertFalse(dynamic + errors); self.assertFalse(second)
            self.assertEqual({'test_first'}, {r.provenance.module for r in first})
            # The next build must see a changed receiver mode in that same helper.
            local.write_text('class Fixture:\n @classmethod\n def setUpClass(cls):\n'
                '  cls.db=CardDatabase(DB_PATH)\n', encoding='utf-8')
            changed, dynamic, errors = owner.discover_module_requirements('test_second', root=root)
            self.assertFalse(dynamic + errors); self.assertEqual({'Second Card'}, {r.value for r in changed})

    def test_cached_source_facts_are_immutable_and_root_dependent_values_stay_separate(self):
        from scripts import compact_ci_dependencies as owner

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); helper = self.sources(root)
            scope = owner.DependencySourceScope(); parsed = scope.files.read(helper)
            environments = scope.environments(parsed, root=root)
            with self.assertRaises(TypeError): environments['new'] = {}
            with self.assertRaises(TypeError): environments['<module>']['ROOT'] = ('polluted',)
            modes = scope.database_modes(parsed)
            with self.assertRaises(TypeError): modes[0]['Injected'] = 'local'
            with self.assertRaises(TypeError): modes[1][('pick', 'db')] = 'local'
            other = scope.environments(parsed, root=root / 'other')
            self.assertEqual((root.as_posix(),), environments['<module>']['ROOT'])
            self.assertEqual(((root / 'other').as_posix(),), other['<module>']['ROOT'])
            first, _, errors = owner.discover_module_requirements('test_first', root=root, sources=scope)
            self.assertFalse(errors); self.assertEqual({'First Card'}, {r.value for r in first})

    def test_fresh_scope_observes_import_additions_removals_and_cycles(self):
        from scripts import compact_ci_dependencies as owner

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); tests = root / 'tests'; tests.mkdir()
            (tests / 'test_case.py').write_text('from extra import pick\ndef test_case(db):\n'
                ' pick(db,"New Card")\n', encoding='utf-8')
            self.assertFalse(owner.discover_module_requirements('test_case', root=root)[0])
            helper = tests / 'extra.py'
            helper.write_text('import middle\ndef pick(db,name):\n return db.lookup(name)\n', encoding='utf-8')
            (tests / 'middle.py').write_text('import extra\n', encoding='utf-8')
            rows, dynamic, errors = owner.discover_module_requirements('test_case', root=root)
            self.assertFalse(dynamic + errors); self.assertEqual({'New Card'}, {r.value for r in rows})
            helper.unlink()
            self.assertFalse(owner.discover_module_requirements('test_case', root=root)[0])
            helper.write_text('def (\n', encoding='utf-8')
            self.assertIn('Cannot parse', owner.discover_module_requirements('test_case', root=root)[2][0])


class FixtureAliasPriorityTests(unittest.TestCase):
    def index(self, root, cards):
        from scripts.compact_ci_dependencies import index_fixture_ownership
        fixture = root / 'cards.json'
        fixture.write_text(json.dumps({'schema_version': 1, 'cards': cards, 'rulings': []}), encoding='utf-8')
        return index_fixture_ownership([fixture], root=root)

    def test_canonical_names_beat_preparation_insets_in_either_fixture_order(self):
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        standalone = card('Seething Song', '00000000-0000-4000-8000-000000000001')
        preparation = card('Blazing Firesinger // Seething Song', '00000000-0000-4000-8000-000000000002')
        preparation.update(layout='prepare', card_faces=[{'name': 'Blazing Firesinger'}, {'name': 'Seething Song'}])
        for ordered in ([standalone, preparation], [preparation, standalone]):
            with self.subTest(first=ordered[0]['name']), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                indexed = self.index(root, ordered)
                self.assertEqual([], indexed['conflicts'])
                self.assertEqual(standalone['oracle_id'], indexed['oracle_by_normalized_name']['seething song'])
                self.assertEqual(preparation['oracle_id'], indexed['oracle_by_normalized_name']['blazing firesinger'])
                build_fixture_database(root / 'cards.json', root / 'cards.sqlite3')
                with CardDatabase(root / 'cards.sqlite3') as db:
                    self.assertEqual(db.lookup('Seething Song', fuzzy=False).oracle_id,
                                     indexed['oracle_by_normalized_name']['seething song'])
                    self.assertEqual(db.lookup('Blazing Firesinger', fuzzy=False).oracle_id,
                                     indexed['oracle_by_normalized_name']['blazing firesinger'])
                    self.assertEqual(db.lookup('Blazing Firesinger // Seething Song', fuzzy=False).oracle_id,
                                     indexed['oracle_by_normalized_name']['blazing firesinger // seething song'])

    def test_game_card_alias_precedes_art_series_in_either_fixture_order(self):
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase

        original = json.loads((ROOT / "tests/fixtures/deck-alias-collision-cards.json").read_bytes())["cards"]
        playable, art = original
        another = {
            **art, "oracle_id": "00000000-0000-4891-8060-00000000da03",
            "name": "Boundary Gate // Boundary Artwork",
        }
        for cards in ((playable, art, another), (art, another, playable)):
            with self.subTest(first=cards[0]["layout"]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                indexed = self.index(root, cards)
                self.assertEqual([], indexed["conflicts"])
                self.assertEqual(playable["oracle_id"], indexed["oracle_by_normalized_name"]["boundary gate"])
                database = root / "cards.sqlite3"
                build_fixture_database(root / "cards.json", database)
                with CardDatabase(database) as db:
                    self.assertEqual(playable["oracle_id"], db.lookup("Boundary Gate", fuzzy=False).oracle_id)

    def test_duplicate_canonical_names_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            indexed = self.index(Path(directory), [card('Same Name', 'first'), card('Same Name', 'second')])
            self.assertTrue(indexed['conflicts'])
            self.assertEqual('normalized_name', indexed['conflicts'][0]['kind'])

    def test_equal_priority_face_aliases_still_fail_closed(self):
        first = card('First // Shared', 'first')
        second = card('Second // Shared', 'second')
        for value in (first, second):
            value['card_faces'] = [{'name': value['name'].split(' // ')[0]}, {'name': 'Shared'}]
        with tempfile.TemporaryDirectory() as directory:
            indexed = self.index(Path(directory), [first, second])
            self.assertTrue(indexed['conflicts'])
            self.assertEqual('alias', indexed['conflicts'][0]['kind'])


class CompactCIDependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = build_dependency_report(root=ROOT)

    def temporary(
        self,
        *,
        cards: list[dict[str, object]],
        source: str,
        declarations: list[dict[str, object]] | None = None,
        exclusions: list[dict[str, str]] | None = None,
        extra_fixtures: dict[str, list[dict[str, object]]] | None = None,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return TemporaryDependencyRepository(
            Path(temporary.name),
            cards=cards,
            source=source,
            declarations=declarations,
            exclusions=exclusions,
            extra_fixtures=extra_fixtures,
        )

    def test_policy_line_endings_preserve_report_identity_and_content_changes_remain_visible(self):
        repository = self.temporary(
            cards=[card("Fixture Card", "00000000-0000-4000-8000-000000000001")],
            source='db.lookup("Fixture Card")\n',
            extra_fixtures={"tests/fixtures/extra.json": [
                card("Additional Card", "00000000-0000-4000-8000-000000000002"),
            ]},
        )
        policies = (
            repository.root / "tests/fixtures/compact-ci-fixtures.json",
            repository.root / "platform/test-shards.json",
        )
        lf_bytes = {path: path.read_bytes().replace(b"\r\n", b"\n") for path in policies}
        for path, content in lf_bytes.items():
            path.write_bytes(content)
        lf_report = repository.report()
        self.assertTrue(lf_report["closed"])
        for path, content in lf_bytes.items():
            path.write_bytes(content.replace(b"\n", b"\r\n"))
        self.assertEqual(lf_report, repository.report())

        manifest = json.loads(policies[0].read_text(encoding="utf-8"))
        manifest["fixtures"].append("tests/fixtures/extra.json")
        policies[0].write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        changed_report = repository.report()
        self.assertTrue(changed_report["closed"])
        self.assertEqual(2, changed_report["card_count"])
        self.assertNotEqual(lf_report["compact_fixture_manifest_fingerprint"],
                            changed_report["compact_fixture_manifest_fingerprint"])

    def test_current_compact_shards_are_closed_with_echo_and_amass_witnesses(self):
        self.assertTrue(self.current["closed"], self.current)
        self.assertEqual([], self.current["dynamic_unresolved_requirements"])
        requirements = {
            row["value"]: row for row in self.current["requirement_provenance"]
        }
        for name in (
            "Karmic Guide",
            "Shah of Naar Isle",
            "Tippy-Toe, Terrific Partner",
        ):
            with self.subTest(name=name):
                self.assertIn(name, requirements)
                self.assertIsNotNone(requirements[name]["resolution"])

    def test_missing_karmic_guide_fails_before_the_echo_shard(self):
        repository = self.temporary(
            cards=[card("Shah of Naar Isle", "00000000-0000-4000-8000-000000000002")],
            source='db.lookup("Karmic Guide")\n',
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual("Karmic Guide", report["missing_cards"][0]["value"])

    def test_missing_shah_of_naar_isle_fails_before_the_echo_shard(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source='db.lookup("Shah of Naar Isle")\n',
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual("Shah of Naar Isle", report["missing_cards"][0]["value"])

    def test_removed_amass_witness_is_reported(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source='db.lookup("Tippy-Toe, Terrific Partner")\n',
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual(
            "Tippy-Toe, Terrific Partner", report["missing_cards"][0]["value"]
        )

    def test_new_literal_lookup_without_provider_is_reported(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source='database.lookup("Unprovided Card")\n',
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual("Unprovided Card", report["missing_cards"][0]["value"])

    def test_dynamic_lookup_requires_an_exact_declaration(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source=(
                "import os\n"
                "class FixtureTests:\n"
                "    def test_dynamic(self):\n"
                "        self.db.lookup(os.environ['CARD'])\n"
            ),
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual(1, len(report["dynamic_unresolved_requirements"]))

    def test_declared_card_must_resolve(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source=(
                "import os\n"
                "class FixtureTests:\n"
                "    def test_dynamic(self):\n"
                "        self.db.lookup(os.environ['CARD'])\n"
            ),
            declarations=[
                {
                    "source": "tests/test_fixture.py",
                    "symbol": "FixtureTests.test_dynamic",
                    "card_names": ["Unprovided Card"],
                    "oracle_ids": [],
                    "deck_files": [],
                    "fixture_files": [],
                    "rationale": "The environment value is the test input.",
                }
            ],
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual([], report["dynamic_unresolved_requirements"])
        self.assertEqual("Unprovided Card", report["missing_cards"][0]["value"])

    def test_referenced_deck_with_missing_card_is_reported(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source=(
                "from pathlib import Path\n"
                "from quorune.deck import DeckLoader\n"
                "ROOT = Path(__file__).resolve().parents[1]\n"
                "DeckLoader(db).load(ROOT / 'examples' / 'fixture.txt')\n"
            ),
        )
        (repository.root / "examples/fixture.txt").write_text(
            "Commander:\n1 Karmic Guide\n\nMainboard:\n1 Missing Deck Card\n",
            encoding="utf-8",
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertIn("Missing Deck Card", report["missing_deck_entries"][0]["error"])

    def test_conflicting_oracle_identity_is_reported(self):
        oracle_id = "00000000-0000-4000-8000-000000000001"
        repository = self.temporary(
            cards=[card("Karmic Guide", oracle_id)],
            source='db.lookup("Karmic Guide")\n',
            extra_fixtures={
                "tests/fixtures/conflict.json": [card("Different Card", oracle_id)]
            },
        )
        manifest_path = repository.root / "tests/fixtures/compact-ci-fixtures.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["fixtures"].append("tests/fixtures/conflict.json")
        manifest["fixtures"].sort()
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual("oracle_id", report["conflicting_fixture_identities"][0]["kind"])

    def test_full_database_only_module_cannot_remain_in_a_compact_shard(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source='db.lookup("Karmic Guide")\n',
            exclusions=[
                {
                    "module": "test_fixture",
                    "reason": "This mutation proves compact assignment fails closed.",
                }
            ],
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual(
            ["test_fixture"],
            report["full_database_only_modules_assigned_to_compact_shards"],
        )

    def test_required_fixture_must_be_owned_by_the_canonical_manifest(self):
        repository = self.temporary(
            cards=[card("Karmic Guide", "00000000-0000-4000-8000-000000000001")],
            source=(
                "import os\n"
                "class FixtureTests:\n"
                "    def test_dynamic(self):\n"
                "        self.db.lookup(os.environ['CARD'])\n"
            ),
            declarations=[
                {
                    "source": "tests/test_fixture.py",
                    "symbol": "FixtureTests.test_dynamic",
                    "card_names": ["Karmic Guide"],
                    "oracle_ids": [],
                    "deck_files": [],
                    "fixture_files": ["tests/fixtures/orphan.json"],
                    "rationale": "The dynamic witness is owned by the orphan fixture.",
                }
            ],
            extra_fixtures={
                "tests/fixtures/orphan.json": [
                    card("Orphan", "00000000-0000-4000-8000-000000000099")
                ]
            },
        )
        report = repository.report()
        self.assertFalse(report["closed"])
        self.assertEqual(
            "tests/fixtures/orphan.json",
            report["missing_fixture_requirements"][0]["fixture_file"],
        )


if __name__ == "__main__":
    unittest.main()
