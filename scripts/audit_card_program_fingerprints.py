from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.card_programs.current_view_equivalence import reviewed_rulings_match
from quorune.card_programs.reviewed_overlay import shadowed_reviewed_program_keys
from quorune.card_programs.validation import reviewed_dungeon_room_oracle_matches
from quorune.carddb import CardDatabase
from quorune.compiler.program_generation import generated_programs, rulings_source_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram, SemanticRegistry


PROFILE = "commander_review"


def trusted_program_groups(registry: SemanticRegistry) -> dict[str, list[SemanticProgram]]:
    groups = defaultdict(list)
    for key in registry.keys():
        program = registry.get(key)
        if program is not None and program.oracle_id and program.trust_level == "trusted":
            groups[program.oracle_id].append(program)
    return dict(groups)


def _verified_equivalences(db, card, programs, capability_registry):
    generated = generated_programs(
        db, card, trust_level="trusted", capability_registry=capability_registry,
        capability_profile=PROFILE,
    )
    return shadowed_reviewed_program_keys(
        card, generated, programs, capability_registry=capability_registry,
        capability_profile=PROFILE,
    )


def _source(program: SemanticProgram) -> dict[str, str | None]:
    return {
        "oracle_hash": program.provenance.get("source_oracle_hash"),
        "rulings_hash": program.provenance.get("source_rulings_hash"),
    }


def _program_row(program: SemanticProgram) -> dict:
    return {
        "key": program.key,
        "version": program.version,
        "authored_by": program.provenance.get("authored_by"),
        "stored": _source(program),
    }


def audit_snapshot(database: Path, groups: dict[str, list[SemanticProgram]]) -> dict:
    """Compare each retained source once and collect every program mismatch."""
    started = perf_counter()
    mismatches = []
    approved = []
    capabilities = None
    with CardDatabase(database) as db:
        metadata = db.metadata()
        for oracle_id, programs in sorted(groups.items()):
            try:
                card = db.by_oracle_id(oracle_id)
            except KeyError:
                mismatches.append({
                    "oracle_id": oracle_id, "card": programs[0].label, "current": None,
                    "programs": [{**_program_row(p), "fields": ["missing_card"]} for p in programs],
                })
                continue
            current = {
                "oracle_hash": sha256(card.oracle_text.encode("utf-8")).hexdigest(),
                "rulings_hash": rulings_source_hash(db, card),
            }
            needs_equivalence = {
                p.key for p in programs
                if p.provenance.get("source_oracle_hash") == current["oracle_hash"]
                and p.provenance.get("source_rulings_hash") != current["rulings_hash"]
                and reviewed_rulings_match(p, current["rulings_hash"])
            }
            superseded = set()
            equivalence_error = None
            if needs_equivalence:
                capabilities = capabilities or load_default_capability_registry()
                try:
                    superseded = _verified_equivalences(db, card, programs, capabilities)
                except (KeyError, TypeError, ValueError) as error:
                    equivalence_error = str(error)
            errors = []
            for program in programs:
                stored = _source(program)
                fields = [key for key, value in current.items() if stored[key] != value]
                if not fields:
                    continue
                row = _program_row(program)
                if fields == ["oracle_hash"] and reviewed_dungeon_room_oracle_matches(
                    card, program, current["oracle_hash"],
                ):
                    approved.append({"oracle_id": oracle_id, "card": card.name, "current": current, **row})
                    continue
                if fields == ["rulings_hash"] and program.key in needs_equivalence and program.key in superseded:
                    approved.append({"oracle_id": oracle_id, "card": card.name, "current": current, **row})
                else:
                    errors.append({**row, "fields": fields})
            if errors:
                failure = {"oracle_id": oracle_id, "card": card.name, "current": current, "programs": errors}
                if equivalence_error:
                    failure["equivalence_error"] = equivalence_error
                mismatches.append(failure)
    return {
        "database": str(database),
        "snapshot": metadata,
        "cards_checked": len(groups),
        "programs_checked": sum(len(programs) for programs in groups.values()),
        "approved_equivalences": approved,
        "mismatched_cards": len(mismatches),
        "mismatched_programs": sum(len(row["programs"]) for row in mismatches),
        "mismatches": mismatches,
        "duration_seconds": round(perf_counter() - started, 3),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit retained card-program source fingerprints.")
    parser.add_argument("--db", type=Path, action="append",
                        help="Full card SQLite snapshot to check. Repeat to check release and certification data.")
    parser.add_argument("--registry", type=Path,
                        help="Audit a saved program registry in place of the built-in semantic packs.")
    parser.add_argument("--output", type=Path, help="Also save the complete JSON report to this path.")
    args = parser.parse_args(argv)
    databases = args.db or [Path(os.environ.get("MTG_CARD_DB") or ROOT / "data/scryfall-current.sqlite3")]
    for path in databases + ([args.registry] if args.registry else []):
        if not path.is_file():
            parser.error(f"Input file does not exist: {path}")
    registry = SemanticRegistry(args.registry, include_builtin_packs=args.registry is None)
    groups = trusted_program_groups(registry)
    if not groups:
        parser.error("The selected registry contains no trusted card source programs.")
    snapshots = [audit_snapshot(path, groups) for path in databases]
    passed = all(not row["mismatched_cards"] for row in snapshots)
    report = {"schema_version": 1, "status": "pass" if passed else "fail", "snapshots": snapshots}
    output = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8", newline="\n")
    print(output, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
