from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.commands import _CardProgramCoverageCollector
from quorune.compiler.corpus_reporting import _OracleCoverageCollector
from quorune.compiler.target_effect_corpus_assurance import (
    TargetEffectAssuranceError,
    validate_target_effect_assurance,
)
from quorune.oracle_ir import ORACLE_COMPILER_VERSION, compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.util import stable_json
from scripts.validate_python_runtime import require_supported_python


OUTPUTS = {
    "oracle_full": ROOT / "coverage" / "oracle-coverage.json",
    "oracle_commander": ROOT / "coverage" / "oracle-coverage-commander.json",
    "program_full": ROOT / "coverage" / "card-program-coverage.json",
    "program_commander": (
        ROOT / "coverage" / "card-program-coverage-commander.json"
    ),
}


class CompilerCorpusCoverageError(ValueError):
    """The tracked compiler corpus census is malformed or stale."""


def _generate(database: Path) -> dict[str, dict[str, Any]]:
    capabilities = load_default_capability_registry()
    semantics = SemanticRegistry()
    oracle = {scope: _OracleCoverageCollector(capability_registry=capabilities,
        capability_profile="commander_review", residual_limit=20) for scope in (False, True)}
    programs = {scope: _CardProgramCoverageCollector(capabilities, "commander_review")
                for scope in (False, True)}
    with CardDatabase(database) as db:
        commander_ids = {record.oracle_id for record in db.iter_cards(commander_legal_only=True)}
        for record in db.iter_cards():
            scopes = (False, True) if record.oracle_id in commander_ids else (False,)
            ir = compile_oracle_card(record, capability_registry=capabilities,
                                     capability_profile="commander_review")
            for scope in scopes:
                oracle[scope].observe(record, ir)
            try:
                program = _compile_best_available_card_program_from_ir(db, record, ir,
                    semantic_registry=semantics, capability_registry=capabilities,
                    capability_profile="commander_review")
            except (KeyError, ValueError) as exc:
                for scope in scopes:
                    programs[scope].observe(record, error=exc)
            else:
                for scope in scopes:
                    programs[scope].observe(record, program)
        return {
            "oracle_full": oracle[False].report(db, commander_legal_only=False),
            "oracle_commander": oracle[True].report(db, commander_legal_only=True),
            "program_full": programs[False].report(db, commander_legal_only=False, limit=None),
            "program_commander": programs[True].report(db, commander_legal_only=True, limit=None),
        }


def _load() -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for name, path in OUTPUTS.items():
        if not path.is_file():
            raise CompilerCorpusCoverageError(
                f"Missing compiler corpus coverage output: {path}"
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CompilerCorpusCoverageError(
                f"Invalid compiler corpus coverage output: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise CompilerCorpusCoverageError(
                f"Compiler corpus coverage output must be an object: {path}"
            )
        reports[name] = value
    return reports


def _status_total(report: Mapping[str, Any]) -> int:
    statuses = report.get("status_counts")
    if not isinstance(statuses, Mapping) or any(
        type(value) is not int or value < 0 for value in statuses.values()
    ):
        raise CompilerCorpusCoverageError(
            "Compiler corpus coverage status_counts are malformed"
        )
    return sum(statuses.values())


def validate_reports(reports: Mapping[str, Mapping[str, Any]]) -> None:
    if set(reports) != set(OUTPUTS):
        raise CompilerCorpusCoverageError(
            "Compiler corpus coverage report set is incomplete"
        )
    capabilities = load_default_capability_registry()
    expected_registry = capabilities.fingerprint
    expected_evidence = capabilities.evidence_fingerprint
    pairs = (
        ("oracle_full", "program_full", False),
        ("oracle_commander", "program_commander", True),
    )
    snapshots: list[Mapping[str, Any]] = []
    for oracle_name, program_name, commander_only in pairs:
        oracle = reports[oracle_name]
        program = reports[program_name]
        if (
            oracle.get("compiler_version") != ORACLE_COMPILER_VERSION
            or program.get("compiler_version") != ORACLE_COMPILER_VERSION
        ):
            raise CompilerCorpusCoverageError(
                "Compiler corpus coverage compiler version is stale"
            )
        for report in (oracle, program):
            if report.get("capability_registry_fingerprint") != expected_registry:
                raise CompilerCorpusCoverageError(
                    "Compiler corpus coverage capability registry is stale"
                )
            if report.get("capability_evidence_fingerprint") != expected_evidence:
                raise CompilerCorpusCoverageError(
                    "Compiler corpus coverage capability evidence is stale"
                )
            if report.get("commander_legal_only") is not commander_only:
                raise CompilerCorpusCoverageError(
                    "Compiler corpus coverage profile scope is malformed"
                )
        if oracle.get("capability_profile") != "commander_review" or (
            program.get("profile") != "commander_review"
        ):
            raise CompilerCorpusCoverageError(
                "Compiler corpus coverage capability profile is stale"
            )
        oracle_count = oracle.get("total_oracle_ids")
        program_count = program.get("cards_considered")
        if (
            type(oracle_count) is not int
            or oracle_count < 1
            or program_count != oracle_count
            or _status_total(oracle) != oracle_count
            or _status_total(program) != program_count
        ):
            raise CompilerCorpusCoverageError(
                "Compiler corpus coverage card counts are inconsistent"
            )
        if program.get("card_data_snapshot") != oracle.get(
            "card_data_snapshot"
        ):
            raise CompilerCorpusCoverageError(
                "Compiler corpus coverage card snapshots disagree"
            )
        assurance = oracle.get("target_effect_corpus_assurance")
        if not isinstance(assurance, Mapping):
            raise CompilerCorpusCoverageError(
                "Compiler corpus target-effect assurance is missing"
            )
        try:
            validate_target_effect_assurance(
                assurance,
                compiler_version=ORACLE_COMPILER_VERSION,
                capability_registry=capabilities,
                capability_profile="commander_review",
                card_data_snapshot=oracle["card_data_snapshot"],
                commander_legal_only=commander_only,
            )
        except TargetEffectAssuranceError as exc:
            raise CompilerCorpusCoverageError(str(exc)) from exc
        snapshots.append(oracle["card_data_snapshot"])
    if snapshots[0] != snapshots[1]:
        raise CompilerCorpusCoverageError(
            "Full and Commander compiler corpus snapshots disagree"
        )
    if reports["oracle_commander"]["total_oracle_ids"] > reports[
        "oracle_full"
    ]["total_oracle_ids"]:
        raise CompilerCorpusCoverageError(
            "Commander corpus cannot exceed the full corpus"
        )


def _canonical_text(value: Mapping[str, Any]) -> str:
    return stable_json(value)


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser(
        description=(
            "Write or verify the four pinned Oracle/CardProgram corpus "
            "coverage reports"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()

    if args.write:
        if args.db is None:
            parser.error("--write requires --db")
        reports = _generate(args.db)
        validate_reports(reports)
        for name, path in OUTPUTS.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _canonical_text(reports[name]),
                encoding="utf-8",
                newline="\n",
            )
    else:
        reports = _load()
        validate_reports(reports)
        for name, path in OUTPUTS.items():
            if path.read_text(encoding="utf-8") != _canonical_text(
                reports[name]
            ):
                raise CompilerCorpusCoverageError(
                    f"Compiler corpus coverage output is not canonical: {path}"
                )
    print(
        stable_json(
            {
                "ok": True,
                "compiler_version": ORACLE_COMPILER_VERSION,
                "full_cards": reports["oracle_full"]["total_oracle_ids"],
                "commander_cards": reports["oracle_commander"][
                    "total_oracle_ids"
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
