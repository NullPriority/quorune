from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.carddb import CardDatabase, CardRecord, build_card_database
from quorune.deck import DeckLoader
from quorune.util import stable_json
from scripts.compact_ci_dependencies import (
    fixture_paths as dependency_fixture_paths,
)
from scripts.compact_ci_report import build_dependency_report


COMPACT_CI_FIXTURE_MANIFEST = (
    ROOT / "tests" / "fixtures" / "compact-ci-fixtures.json"
)
COMPACT_CI_SCRIPT_CONSUMERS = (
    "scripts/local_merge_gate.py",
    "scripts/quick_gate.py",
)


def compact_ci_fixture_paths(
    manifest_path: Path | None = None,
    *,
    root: Path = ROOT,
) -> tuple[Path, ...]:
    """Load the one canonical compact-CI fixture set, failing closed."""

    if manifest_path is None:
        manifest_path = root / "tests" / "fixtures" / "compact-ci-fixtures.json"
    return dependency_fixture_paths(manifest_path, root=root)


def validate_compact_ci_consumers(*, root: Path = ROOT) -> dict[str, object]:
    """Prove every compact database consumer uses the manifest-backed command."""

    workflow_root = root / ".github" / "workflows"
    workflow_consumers = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for pattern in ("*.yml", "*.yaml")
            for path in workflow_root.glob(pattern)
            if "scripts/build_test_database.py"
            in path.read_text(encoding="utf-8")
        )
    )
    consumers = (*workflow_consumers, *COMPACT_CI_SCRIPT_CONSUMERS)
    invocation_count = 0
    for relative in consumers:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        command_sites = [
            index
            for index, line in enumerate(lines)
            if "scripts/build_test_database.py" in line
        ]
        if not command_sites:
            raise ValueError(f"Compact CI consumer has no database build: {relative}")
        allowed = ("validate-ci-dependencies", "validate-ci", "build-ci")
        commands = []
        for index in command_sites:
            window = " ".join(lines[index : index + 5])
            commands.append(next((name for name in allowed if name in window), None))
        if any(command is None for command in commands) or "--fixture" in text:
            raise ValueError(
                "Compact CI consumer maintains an independent fixture list: "
                f"{relative}"
            )
        build_count = sum(command == "build-ci" for command in commands)
        if not build_count:
            raise ValueError(f"Compact CI consumer has no database build: {relative}")
        invocation_count += build_count
    fixtures = compact_ci_fixture_paths(root=root)
    return {
        "ok": True,
        "manifest": (
            root / "tests" / "fixtures" / "compact-ci-fixtures.json"
        ).relative_to(root).as_posix(),
        "fixtures": [path.relative_to(root).as_posix() for path in fixtures],
        "consumers": list(consumers),
        "invocations": invocation_count,
    }


def _card_payload(card: CardRecord) -> dict:
    return {
        "oracle_id": card.oracle_id,
        "name": card.name,
        "mana_cost": card.mana_cost,
        "cmc": card.mana_value,
        "type_line": card.type_line,
        "oracle_text": card.oracle_text,
        "power": card.power,
        "toughness": card.toughness,
        "loyalty": card.loyalty,
        "defense": card.defense,
        "colors": list(card.colors),
        "color_identity": list(card.color_identity),
        "keywords": list(card.keywords),
        "produced_mana": list(card.produced_mana),
        "layout": card.layout,
        "released_at": card.released_at,
        "legalities": card.legalities,
        **({"card_faces": list(card.faces)} if card.faces else {}),
    }


def export_fixture(
    source_db: Path,
    output: Path,
    deck_paths: list[Path],
    extra_names: list[str],
) -> dict:
    with CardDatabase(source_db) as db:
        loader = DeckLoader(db)
        names = set(extra_names)
        for deck_path in deck_paths:
            deck = loader.load(deck_path)
            names.update(
                entry.name
                for entry in deck.entries
                if entry.board in {"mainboard", "commander"}
            )
        cards = {
            card.oracle_id: card
            for card in (db.lookup(name) for name in sorted(names))
        }
        rulings = [
            {
                "oracle_id": ruling.oracle_id,
                "published_at": ruling.published_at,
                "source": ruling.source,
                "comment": ruling.comment,
            }
            for card in cards.values()
            for ruling in db.rulings(card)
        ]
    payload = {
        "schema_version": 1,
        "fixture_kind": "public_exact-list_card_data",
        "source": "Scryfall Oracle/rulings subset for offline tests",
        "cards": [
            _card_payload(card)
            for card in sorted(cards.values(), key=lambda value: value.name)
        ],
        "rulings": sorted(
            rulings,
            key=lambda value: (
                value["oracle_id"],
                value["published_at"],
                value["comment"],
            ),
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(stable_json(payload) + "\n", encoding="utf-8")
    return {
        "output": str(output),
        "cards": len(payload["cards"]),
        "rulings": len(payload["rulings"]),
    }


def build_fixture_database(
    fixtures: Path | list[Path], output: Path
) -> dict:
    fixture_paths = [fixtures] if isinstance(fixtures, Path) else fixtures
    cards_by_oracle: dict[str, dict] = {}
    oracle_by_name: dict[str, str] = {}
    ruling_counts: Counter[str] = Counter()
    ruling_values: dict[str, dict] = {}
    for fixture in fixture_paths:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        if int(payload.get("schema_version", 0)) != 1:
            raise ValueError(
                f"Unsupported public card fixture schema: {fixture}"
            )
        for card in payload.get("cards", []):
            oracle_id = str(card.get("oracle_id") or "")
            name = str(card.get("name") or "")
            if not oracle_id or not name:
                raise ValueError(f"Card fixture entry is missing identity: {fixture}")
            existing = cards_by_oracle.get(oracle_id)
            if existing is not None and existing != card:
                raise ValueError(
                    f"Conflicting card fixture for Oracle ID {oracle_id}"
                )
            named_oracle = oracle_by_name.get(name.casefold())
            if named_oracle is not None and named_oracle != oracle_id:
                raise ValueError(f"Conflicting card fixture name: {name}")
            cards_by_oracle[oracle_id] = card
            oracle_by_name[name.casefold()] = oracle_id
        # Preserve a snapshot's genuine multiplicity without multiplying the
        # same publication when several overlapping fixtures contain it.
        snapshot_counts = Counter()
        for ruling in payload.get("rulings", []):
            identity = stable_json(ruling)
            ruling_values[identity] = ruling
            snapshot_counts[identity] += 1
        ruling_counts |= snapshot_counts
    ruling_rows = [ruling_values[identity] for identity, count in ruling_counts.items() for _ in range(count)]
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        oracle_path = work / "oracle-cards.jsonl"
        rulings_path = work / "rulings.jsonl"
        oracle_path.write_text(
            "".join(
                json.dumps(card, sort_keys=True, separators=(",", ":")) + "\n"
                for card in sorted(
                    cards_by_oracle.values(),
                    key=lambda value: (value["name"], value["oracle_id"]),
                )
            ),
            encoding="utf-8",
        )
        rulings_path.write_text(
            "".join(
                json.dumps(ruling, sort_keys=True, separators=(",", ":")) + "\n"
                for ruling in sorted(
                    ruling_rows,
                    key=lambda value: (
                        str(value.get("oracle_id") or ""),
                        str(value.get("published_at") or ""),
                        str(value.get("source") or ""),
                        str(value.get("comment") or ""),
                    ),
                )
            ),
            encoding="utf-8",
        )
        result = build_card_database(
            oracle_path,
            rulings_path,
            output,
            overwrite=True,
        )
    result["fixture"] = str(fixture_paths[0])
    result["fixtures"] = [str(fixture) for fixture in fixture_paths]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export or build the compact, public CI card-data fixture."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export")
    export.add_argument("--source-db", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--deck", action="append", type=Path, default=[])
    export.add_argument("--extra-card", action="append", default=[])

    build = subparsers.add_parser("build")
    build.add_argument(
        "--fixture", required=True, action="append", type=Path
    )
    build.add_argument("--output", required=True, type=Path)

    build_ci = subparsers.add_parser("build-ci")
    build_ci.add_argument("--output", required=True, type=Path)

    subparsers.add_parser("validate-ci")
    subparsers.add_parser("validate-ci-dependencies")

    args = parser.parse_args()
    if args.command == "export":
        deck_paths = args.deck or [
            ROOT / "examples" / "mishra-eminent-one.txt",
            ROOT / "examples" / "zimone-and-dina.txt",
        ]
        result = export_fixture(
            args.source_db,
            args.output,
            deck_paths,
            args.extra_card,
        )
    elif args.command == "build":
        result = build_fixture_database(args.fixture, args.output)
    elif args.command == "build-ci":
        result = build_fixture_database(
            list(compact_ci_fixture_paths()),
            args.output,
        )
        result["fixture_manifest"] = (
            COMPACT_CI_FIXTURE_MANIFEST.relative_to(ROOT).as_posix()
        )
    elif args.command == "validate-ci":
        result = validate_compact_ci_consumers()
    else:
        result = build_dependency_report(root=ROOT)
        if not result["closed"]:
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1
        result = {
            "closed": True,
            "modules_inspected": result["modules_inspected"],
            "requirements_discovered": result["requirements_discovered"],
            "requirements_explicitly_declared": result[
                "requirements_explicitly_declared"
            ],
            "fixture_count": result["fixture_count"],
            "card_count": result["card_count"],
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
