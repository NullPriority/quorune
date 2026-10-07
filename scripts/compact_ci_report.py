from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Mapping

from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from scripts.compact_ci_dependencies import (
    DynamicSite,
    Provenance,
    Requirement,
    discover_module_requirements,
    index_fixture_ownership,
    load_dependency_manifest,
)
from scripts.source_tree_fingerprint import (
    SOURCE_TREE_FINGERPRINT_ALGORITHM,
    tracked_worktree_source_fingerprint,
)
from scripts.test_shards import load_manifest as load_test_shards


REPORT_SCHEMA_VERSION = 1


def _policy_text_fingerprint(path: Path) -> str:
    """Bind policy content without depending on checkout line endings."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _declared_requirements(
    module: str,
    site: DynamicSite,
    declaration: Mapping[str, object],
) -> tuple[Requirement, ...]:
    provenance = Provenance(module, site.source, site.symbol, site.line)
    values_by_kind = {
        "card_name": declaration["card_names"],
        "oracle_id": declaration["oracle_ids"],
        "deck_file": declaration["deck_files"],
        "fixture_file": declaration["fixture_files"],
    }
    return tuple(
        Requirement(kind, str(value), "declared", provenance)
        for kind, values in values_by_kind.items()
        for value in values
    )


def _shard_modules(shards: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    return {
        str(name): tuple(str(module) for module in modules)
        for group in ("primary_shards", "overlay_suites")
        for name, modules in shards[group].items()
    }


def build_dependency_report(
    *,
    root: Path,
    manifest_path: Path | None = None,
    shard_path: Path | None = None,
    source_fingerprint: str | None = None,
) -> dict[str, object]:
    root = root.resolve()
    manifest_path = manifest_path or root / "tests/fixtures/compact-ci-fixtures.json"
    shard_path = shard_path or root / "platform/test-shards.json"
    manifest = load_dependency_manifest(manifest_path, root=root)
    paths = tuple(root / relative for relative in manifest["fixtures"])
    fixture_index = index_fixture_ownership(paths, root=root)
    shards = load_test_shards(shard_path)
    shard_modules = _shard_modules(shards)
    compact_modules = tuple(
        sorted({module for modules in shard_modules.values() for module in modules})
    )
    excluded = {
        str(row["module"]): str(row["reason"])
        for row in manifest["full_database_only"]
    }
    assigned_exclusions = sorted(set(compact_modules).intersection(excluded))

    declarations = {
        (str(row["source"]), str(row["symbol"])): row
        for row in manifest["dynamic_requirements"]
    }
    used_declarations: set[tuple[str, str]] = set()
    requirements_by_module: dict[str, set[Requirement]] = {}
    unresolved: list[DynamicSite] = []
    discovery_errors: list[str] = []
    for module in compact_modules:
        discovered, dynamic_sites, errors = discover_module_requirements(
            module, root=root
        )
        discovery_errors.extend(errors)
        module_requirements = set(discovered)
        for site in dynamic_sites:
            identity = (site.source, site.symbol)
            declaration = declarations.get(identity)
            if declaration is None:
                unresolved.append(site)
                continue
            used_declarations.add(identity)
            module_requirements.update(
                _declared_requirements(module, site, declaration)
            )
        requirements_by_module[module] = module_requirements
    stale_declarations = sorted(
        f"{source}::{symbol}"
        for source, symbol in set(declarations).difference(used_declarations)
    )

    all_requirements = tuple(
        sorted(
            {
                requirement
                for values in requirements_by_module.values()
                for requirement in values
            }
        )
    )
    missing_cards: list[dict[str, object]] = []
    missing_decks: list[dict[str, object]] = []
    missing_fixtures: list[dict[str, object]] = []
    resolutions: dict[tuple[str, str], dict[str, object]] = {}
    attempted: set[tuple[str, str]] = set()
    fixture_set = set(manifest["fixtures"])
    if not fixture_index["conflicts"]:
        from scripts.build_test_database import build_fixture_database

        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "compact-ci.sqlite3"
            build_fixture_database(list(paths), database_path)
            with CardDatabase(database_path) as database:
                loader = DeckLoader(database)
                for requirement in all_requirements:
                    identity = (requirement.kind, requirement.value)
                    if identity in attempted:
                        continue
                    attempted.add(identity)
                    if requirement.kind == "card_name":
                        try:
                            card = database.lookup(requirement.value, fuzzy=False)
                        except KeyError as exc:
                            missing_cards.append(
                                {
                                    "kind": requirement.kind,
                                    "value": requirement.value,
                                    "error": str(exc),
                                }
                            )
                        else:
                            resolutions[identity] = {
                                "canonical_name": card.name,
                                "oracle_id": card.oracle_id,
                                "fixture_owners": fixture_index[
                                    "owners_by_oracle"
                                ].get(card.oracle_id, []),
                            }
                    elif requirement.kind == "oracle_id":
                        try:
                            card = database.by_oracle_id(requirement.value)
                        except KeyError as exc:
                            missing_cards.append(
                                {
                                    "kind": requirement.kind,
                                    "value": requirement.value,
                                    "error": str(exc),
                                }
                            )
                        else:
                            resolutions[identity] = {
                                "canonical_name": card.name,
                                "oracle_id": card.oracle_id,
                                "fixture_owners": fixture_index[
                                    "owners_by_oracle"
                                ].get(card.oracle_id, []),
                            }
                    elif requirement.kind == "deck_file":
                        deck_path = root / requirement.value
                        if not deck_path.is_file():
                            missing_decks.append(
                                {
                                    "deck_file": requirement.value,
                                    "error": "deck file does not exist",
                                }
                            )
                            continue
                        try:
                            deck = loader.load(deck_path)
                        except (KeyError, ValueError) as exc:
                            missing_decks.append(
                                {
                                    "deck_file": requirement.value,
                                    "error": str(exc),
                                }
                            )
                        else:
                            resolutions[identity] = {
                                "deck_file": requirement.value,
                                "entries": len(deck.entries),
                            }
                    elif requirement.kind == "fixture_file":
                        if requirement.value not in fixture_set:
                            missing_fixtures.append(
                                {
                                    "fixture_file": requirement.value,
                                    "error": (
                                        "required fixture is omitted from the "
                                        "canonical manifest"
                                    ),
                                }
                            )
                        else:
                            resolutions[identity] = {
                                "fixture_file": requirement.value
                            }

    requirement_rows = []
    for requirement in all_requirements:
        row = requirement.to_dict()
        row["resolution"] = resolutions.get((requirement.kind, requirement.value))
        requirement_rows.append(row)
    problem_modules = {site.module for site in unresolved} | {
        str(row["provenance"]["module"])
        for row in requirement_rows
        if row["resolution"] is None
    }
    closure_by_shard = [
        {
            "shard": shard,
            "modules": len(modules),
            "closed": not bool(set(modules).intersection(problem_modules)),
            "problem_modules": sorted(set(modules).intersection(problem_modules)),
        }
        for shard, modules in sorted(shard_modules.items())
    ]
    closed = not any(
        (
            fixture_index["conflicts"],
            unresolved,
            discovery_errors,
            stale_declarations,
            missing_cards,
            missing_decks,
            missing_fixtures,
            assigned_exclusions,
            any(not row["closed"] for row in closure_by_shard),
        )
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_tree_fingerprint_algorithm": SOURCE_TREE_FINGERPRINT_ALGORITHM,
        "source_tree_fingerprint": (
            source_fingerprint or tracked_worktree_source_fingerprint(root)
        ),
        "compact_fixture_manifest": manifest_path.relative_to(root).as_posix(),
        "compact_fixture_manifest_fingerprint": _policy_text_fingerprint(manifest_path),
        "test_shard_manifest": shard_path.relative_to(root).as_posix(),
        "test_shard_fingerprint": _policy_text_fingerprint(shard_path),
        "fixture_count": len(paths),
        "card_count": fixture_index["card_count"],
        "ruling_count": fixture_index["ruling_count"],
        "modules_inspected": len(compact_modules),
        "requirements_discovered": sum(
            requirement.discovery == "static" for requirement in all_requirements
        ),
        "requirements_explicitly_declared": sum(
            requirement.discovery == "declared" for requirement in all_requirements
        ),
        "requirement_provenance": requirement_rows,
        "fixture_ownership": fixture_index["cards"],
        "dynamic_unresolved_requirements": [
            site.to_dict() for site in sorted(unresolved)
        ],
        "missing_cards": sorted(
            missing_cards, key=lambda row: (str(row["kind"]), str(row["value"]))
        ),
        "missing_deck_entries": sorted(
            missing_decks, key=lambda row: str(row["deck_file"])
        ),
        "missing_fixture_requirements": sorted(
            missing_fixtures, key=lambda row: str(row["fixture_file"])
        ),
        "conflicting_fixture_identities": fixture_index["conflicts"],
        "discovery_errors": sorted(set(discovery_errors)),
        "stale_dynamic_declarations": stale_declarations,
        "modules_excluded_as_full_database_only": [
            {"module": module, "reason": reason}
            for module, reason in sorted(excluded.items())
        ],
        "full_database_only_modules_assigned_to_compact_shards": assigned_exclusions,
        "closure_by_shard": closure_by_shard,
        "closed": closed,
    }


def report_markdown(report: Mapping[str, object]) -> str:
    state = "closed" if report["closed"] else "open"
    lines = [
        "---",
        'title: "Compact CI card dependencies"',
        'status: "generated"',
        (
            'authoritative_source: "tests/fixtures/compact-ci-fixtures.json and '
            'platform/test-shards.json"'
        ),
        f'verified: "{report_fingerprint(report)}"',
        'audience: "maintainers and contributors"',
        'maintenance: "generated"',
        "---",
        "",
        "# Compact CI card dependencies",
        "",
        "This report measures whether every test module assigned to a compact-card",
        "database shard has a statically discovered or explicitly declared card and",
        "deck dependency that resolves through the canonical fixture manifest.",
        "",
        f"Overall closure: **{state}**.",
        "",
        "| Measure | Value |",
        "| --- | ---: |",
        f"| Fixture files | {report['fixture_count']} |",
        f"| Cards | {report['card_count']} |",
        f"| Rulings | {report['ruling_count']} |",
        f"| Modules inspected | {report['modules_inspected']} |",
        f"| Static requirements | {report['requirements_discovered']} |",
        f"| Declared dynamic requirements | {report['requirements_explicitly_declared']} |",
        f"| Unresolved dynamic sites | {len(report['dynamic_unresolved_requirements'])} |",
        f"| Missing cards | {len(report['missing_cards'])} |",
        f"| Missing deck dependencies | {len(report['missing_deck_entries'])} |",
        f"| Fixture identity conflicts | {len(report['conflicting_fixture_identities'])} |",
        "",
        "## Shard closure",
        "",
        "| Shard | Modules | Status |",
        "| --- | ---: | --- |",
    ]
    for row in report["closure_by_shard"]:
        lines.append(
            f"| {row['shard']} | {row['modules']} | "
            f"{'closed' if row['closed'] else 'open'} |"
        )
    lines.extend(
        [
            "",
            "The JSON companion contains canonical identities, fixture owners, source",
            "provenance, unresolved dynamics, and exact missing dependencies.",
            "",
        ]
    )
    return "\n".join(lines)


def report_fingerprint(report: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "build_dependency_report",
    "report_fingerprint",
    "report_markdown",
]
