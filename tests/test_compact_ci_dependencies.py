from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from scripts.compact_ci_report import build_dependency_report


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
