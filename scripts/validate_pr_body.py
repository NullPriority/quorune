from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from scripts.pr_evidence import (
    build_pr_evidence,
    pr_evidence_markdown_fields,
    PullRequestEvidenceError,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".github" / "pull_request_template.md"

REQUIRED_SECTIONS = (
    "Summary",
    "Change class and authority",
    "Ownership and implementation",
    "Generated base/head evidence",
    "Evidence",
    "Generated artifacts",
    "Documentation and decisions",
    "Limitations and rollback",
    "Safety checklist",
)

REQUIRED_FIELDS = {
    "Change class and authority": (
        "Change class",
        "Governing rules or capabilities",
        "Oracle/rulings snapshot",
        "Supported profile affected",
    ),
    "Ownership and implementation": (
        "Owner before",
        "Owner after",
        "Duplicate or superseded paths removed",
        "`CommanderEngine` delta",
        "Direct authoritative-write delta",
        "Prohibited identity-dispatch delta",
        "Oracle-ID literal delta",
        "Compiler/CardProgram changes",
        "Card, residual, and capability-closure deltas",
    ),
    "Generated base/head evidence": (
        "Represented family IDs",
        "Represented capability IDs",
        "Exact head SHA",
        "Compiler version delta",
        "CardProgram schema delta",
        "Exact, trusted, and capability-closed card delta",
        "Partial, unresolved, and failed card delta",
        "Oracle and CardProgram ability delta",
        "Executable trust transitions",
        "Structural carrier delta and reconciliation",
        "Oracle and CardProgram material residual delta",
        "Interaction coverage delta",
        "Actual CommanderEngine line delta",
        "Reviewed architecture-baseline delta",
        "Direct authoritative-write delta",
        "Runtime-text delta",
        "Printed-name and Oracle-ID delta",
        "Production, test, and generated line delta",
        "Evidence fingerprint",
        "Evidence command",
    ),
    "Generated artifacts": (
        "Source inputs changed",
        "Generators run",
        "Outputs changed",
        "Freshness checks",
    ),
    "Documentation and decisions": (
        "Current documents changed",
        "ADR added or superseded",
        "Changelog effect",
    ),
    "Limitations and rollback": (
        "Exact remaining limitations",
        "Rollback plan",
        "Compatibility or migration risk",
    ),
}

EVIDENCE_CLASSES = (
    "Focused regression and affected module",
    "Multiplayer/APNAP and interactions",
    "Replay, byte/hash, and compatibility",
    "Privacy and capability isolation",
    "Transaction rollback and malformed input",
    "Headless browser and protocol",
    "Property and fuzz",
    "Focused mutation",
    "Compiler/corpus and residuals",
    "Architecture, ownership, and identity flow",
    "Local quick gate",
    "Required exact-head CI",
)

DURABLE_STATUS_SOURCES = (
    "platform/readiness-source.json",
    "platform/architecture-audit-source.json",
)

_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_NEGATIVE_GENERATED_VALUE = re.compile(
    r"^(?:none|unchanged|not applicable|n/?a)(?:\s*[-—:]\s*.+)?[.!]?$",
    re.IGNORECASE,
)
_FINALIZER_WRITE = re.compile(
    r"scripts[\\/]finalize_generated\.py\s+--write\b", re.IGNORECASE
)
_ACTIONS_RUN_URL = re.compile(
    r"https://github\.com/[^\s)]+/actions/runs/\d+", re.IGNORECASE
)
_EXACT_LOCAL_COMMAND = re.compile(
    r"`[^`\n]*(?:"
    r"(?:\.venv[\\/].*?python(?:\.exe)?)|"
    r"(?:python\s+(?:-m\s+)?(?:unittest|pytest))|"
    r"(?:scripts[\\/](?:test_shards|quick_gate|finalize_generated)\.py)|"
    r"(?:npm|npx|pwsh)\s+"
    r")[^`\n]*`",
    re.IGNORECASE,
)
_NUMERIC_TEST_RESULT = re.compile(
    r"\b\d[\d,]*\s+(?:tests?|test cases?|checks?)\s+passed\b", re.IGNORECASE
)
_BROAD_SUCCESS_CLAIM = re.compile(
    r"(?:"
    r"\b(?:all|full|complete|entire|broad|required|exact-head)\b"
    r"[^\n]{0,80}\b(?:tests?|suite|validation|checks?|ci|actions)\b"
    r"[^\n]{0,40}\b(?:passed|green|successful|succeeded)\b"
    r"|"
    r"\b(?:tests?|suite|validation|checks?|ci|actions)\b"
    r"[^\n]{0,80}\b(?:all|fully|completely)\b"
    r"[^\n]{0,30}\b(?:passed|green|successful|succeeded)\b"
    r")",
    re.IGNORECASE,
)
_CI_WORD = re.compile(r"\b(?:ci|actions|exact-head|required checks?)\b", re.IGNORECASE)
_VOLATILE_KEY = re.compile(
    r"(?:^|_)(?:"
    r"baseline_main_commit|certified_head_sha|exact_head_sha|feature_head_sha|"
    r"head_sha|merge_sha|pull_request|run_id|runtime_branch|workflow_run"
    r")(?:$|_)",
    re.IGNORECASE,
)
_VOLATILE_TEXT = (
    re.compile(r"\bPR\s*#?\d+\b", re.IGNORECASE),
    re.compile(r"https://github\.com/[^\s\"]+/actions/runs/\d+", re.IGNORECASE),
    re.compile(
        r"\b(?:actions|ci|exact-head|main-smoke|workflow)?\s*run\s*#?\d{5,}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:merge\s+commit|exact\s+head|head\s+sha|commit\s+sha)\s+"
        r"[0-9a-f]{7,40}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:assurance|automation|brand|browser|chore|ci|docs|feat|fix|rules|test)"
        r"/[a-z0-9._/-]+$",
        re.IGNORECASE,
    ),
)
_DURABLE_REPOSITORY_PATH = re.compile(
    r"^(?:\.github|docs|platform|quorune|schemas|scripts|server|tests|web)/"
    r"[A-Za-z0-9._/-]+\.[A-Za-z0-9]+$"
)


@dataclass(frozen=True, slots=True)
class PolicyFailure:
    code: str
    message: str


class PullRequestPolicyError(ValueError):
    """A pull-request event or repository comparison cannot be validated."""


def _normalized_comment(value: str) -> str:
    return " ".join(value.split())


def _template_comments(template: str) -> frozenset[str]:
    return frozenset(
        _normalized_comment(comment) for comment in _HTML_COMMENT.findall(template)
    )


def _sections(body: str) -> tuple[dict[str, str], tuple[str, ...]]:
    matches = list(_HEADING.finditer(body))
    values: dict[str, str] = {}
    duplicates: list[str] = []
    for index, match in enumerate(matches):
        title = match.group(2).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        if title in values:
            duplicates.append(title)
            continue
        values[title] = body[match.end() : end]
    return values, tuple(duplicates)


def _field_values(section: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _HTML_COMMENT.sub("", section).splitlines():
        match = re.match(r"^\s*-\s+([^:]+):\s*(.*?)\s*$", line)
        if match:
            values[match.group(1).strip()] = match.group(2).strip()
    return values


def _has_substantive_content(section: str) -> bool:
    visible = _HTML_COMMENT.sub("", section)
    for line in visible.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        field = re.match(r"^-\s+[^:]+:\s*(.*?)\s*$", stripped)
        if field:
            if field.group(1):
                return True
            continue
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and not all(_TABLE_SEPARATOR.fullmatch(cell) for cell in cells):
                if cells not in (["Class", "Result"],):
                    if any(cells[1:]):
                        return True
            continue
        if stripped.startswith("- [ ]"):
            continue
        return True
    return False


def _evidence_rows(section: str) -> tuple[dict[str, str], tuple[str, ...]]:
    rows: dict[str, str] = {}
    duplicates: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 2:
            continue
        if cells == ["Class", "Result"] or all(
            _TABLE_SEPARATOR.fullmatch(cell) for cell in cells
        ):
            continue
        if cells[0] in rows:
            duplicates.append(cells[0])
        else:
            rows[cells[0]] = cells[1]
    return rows, tuple(duplicates)


def _checklist_items(section: str) -> tuple[dict[str, bool], tuple[str, ...]]:
    items: dict[str, bool] = {}
    duplicates: list[str] = []
    for line in section.splitlines():
        match = re.match(r"^\s*-\s*\[([ xX])\]\s+(.+?)\s*$", line)
        if not match:
            continue
        label = " ".join(match.group(2).split())
        if label in items:
            duplicates.append(label)
        else:
            items[label] = match.group(1).lower() == "x"
    return items, tuple(duplicates)


def _is_bare_not_applicable(value: str) -> bool:
    return bool(re.fullmatch(r"n/?a\s*[.!]?", value.strip(), re.IGNORECASE))


def validate_body(body: str, template: str) -> tuple[PolicyFailure, ...]:
    failures: list[PolicyFailure] = []
    if not body.strip():
        return (PolicyFailure("empty-body", "pull-request description is empty"),)

    body_comments = {
        _normalized_comment(comment) for comment in _HTML_COMMENT.findall(body)
    }
    retained = sorted(body_comments & _template_comments(template))
    if retained:
        failures.append(
            PolicyFailure(
                "template-comment",
                f"remove {len(retained)} untouched pull-request template comment(s)",
            )
        )

    sections, duplicates = _sections(body)
    for title in duplicates:
        if title in REQUIRED_SECTIONS:
            failures.append(
                PolicyFailure(
                    "duplicate-section", f"required section {title!r} appears twice"
                )
            )
    for title in REQUIRED_SECTIONS:
        if title not in sections:
            failures.append(
                PolicyFailure("missing-section", f"required section {title!r} is missing")
            )
        elif not _has_substantive_content(sections[title]):
            failures.append(
                PolicyFailure("empty-section", f"required section {title!r} is empty")
            )

    for title, expected_fields in REQUIRED_FIELDS.items():
        if title not in sections:
            continue
        fields = _field_values(sections[title])
        for field in expected_fields:
            value = fields.get(field, "")
            if not value:
                failures.append(
                    PolicyFailure(
                        "blank-field",
                        f"required field {field!r} in {title!r} is missing or blank",
                    )
                )
            elif _is_bare_not_applicable(value):
                failures.append(
                    PolicyFailure(
                        "bare-not-applicable",
                        f"required field {field!r} in {title!r} uses N/A without a concrete reason",
                    )
                )

    evidence = sections.get("Evidence")
    if evidence is not None:
        rows, duplicate_rows = _evidence_rows(evidence)
        for row in duplicate_rows:
            failures.append(
                PolicyFailure("duplicate-evidence", f"evidence row {row!r} appears twice")
            )
        for row in EVIDENCE_CLASSES:
            if row not in rows:
                failures.append(
                    PolicyFailure("missing-evidence", f"required evidence row {row!r} is missing")
                )
                continue
            result = rows[row].strip()
            if not result:
                failures.append(
                    PolicyFailure("blank-evidence", f"evidence row {row!r} is blank")
                )
            elif _is_bare_not_applicable(result):
                failures.append(
                    PolicyFailure(
                        "bare-not-applicable",
                        f"evidence row {row!r} uses N/A without a concrete reason",
                    )
                )

    generated = sections.get("Generated artifacts")
    if generated is not None:
        fields = _field_values(generated)
        claimed_values = (
            fields.get("Source inputs changed", ""),
            fields.get("Outputs changed", ""),
        )
        generated_claimed = any(
            value and not _NEGATIVE_GENERATED_VALUE.fullmatch(value)
            for value in claimed_values
        )
        if generated_claimed and not _FINALIZER_WRITE.search(
            fields.get("Generators run", "")
        ):
            failures.append(
                PolicyFailure(
                    "missing-finalizer",
                    "generated work is claimed but Generators run does not name "
                    "scripts/finalize_generated.py --write",
                )
            )

    safety = sections.get("Safety checklist")
    template_sections, _ = _sections(template)
    template_safety = template_sections.get("Safety checklist", "")
    if safety is not None and template_safety:
        items, duplicates = _checklist_items(safety)
        expected, _ = _checklist_items(template_safety)
        for label in duplicates:
            failures.append(
                PolicyFailure(
                    "duplicate-safety", f"safety assertion {label!r} appears twice"
                )
            )
        for label in expected:
            if label not in items:
                failures.append(
                    PolicyFailure(
                        "missing-safety", f"required safety assertion {label!r} is missing"
                    )
                )
            elif not items[label]:
                failures.append(
                    PolicyFailure(
                        "unchecked-safety",
                        f"complete safety assertion {label!r} or explain it above",
                    )
                )

    visible = _HTML_COMMENT.sub("", body)
    broad_claims = tuple(_BROAD_SUCCESS_CLAIM.finditer(visible))
    if broad_claims:
        has_ci_evidence = bool(_ACTIONS_RUN_URL.search(visible))
        has_local_evidence = bool(
            _EXACT_LOCAL_COMMAND.search(visible) and _NUMERIC_TEST_RESULT.search(visible)
        )
        if any(_CI_WORD.search(claim.group(0)) for claim in broad_claims) and not has_ci_evidence:
            failures.append(
                PolicyFailure(
                    "unsupported-ci-claim",
                    "a broad CI success claim requires its authoritative GitHub Actions run URL",
                )
            )
        has_local_claim = any(
            not _CI_WORD.search(claim.group(0)) for claim in broad_claims
        )
        if has_local_claim and not has_local_evidence:
            failures.append(
                PolicyFailure(
                    "unsupported-local-claim",
                    "a broad local success claim requires an exact command and numeric result",
                )
            )

    return tuple(failures)


def validate_generated_evidence(
    body: str, evidence: Mapping[str, Any]
) -> tuple[PolicyFailure, ...]:
    sections, _duplicates = _sections(body)
    section = sections.get("Generated base/head evidence")
    if section is None:
        return ()
    actual = _field_values(section)
    expected = pr_evidence_markdown_fields(evidence)
    failures = []
    for label, value in expected.items():
        if actual.get(label) != value:
            failures.append(
                PolicyFailure(
                    "stale-pr-evidence",
                    f"generated base/head field {label!r} does not match "
                    "scripts/pr_evidence.py for the exact PR base and head",
                )
            )
    return tuple(failures)


def _flatten(value: Any, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    if isinstance(value, Mapping):
        result: dict[tuple[str, ...], Any] = {}
        for key in sorted(value):
            result.update(_flatten(value[key], (*path, str(key))))
        return result
    if isinstance(value, list):
        result = {}
        for index, item in enumerate(value):
            result.update(_flatten(item, (*path, str(index))))
        return result
    return {path: value}


def _volatile_status_value(path: tuple[str, ...], value: Any) -> str | None:
    dotted = ".".join(path)
    if dotted == "repository.default_branch":
        return None
    if path and path[0] == "historical_observations":
        return None
    if path and _VOLATILE_KEY.search(path[-1]):
        return f"volatile field {dotted!r}"
    if isinstance(value, str):
        if _DURABLE_REPOSITORY_PATH.fullmatch(value):
            return None
        for pattern in _VOLATILE_TEXT:
            if pattern.search(value):
                return f"volatile provenance in {dotted!r}"
    return None


def validate_status_changes(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    source: str,
) -> tuple[PolicyFailure, ...]:
    prior = _flatten(before)
    failures: list[PolicyFailure] = []
    for path, value in _flatten(after).items():
        if prior.get(path, object()) == value:
            continue
        reason = _volatile_status_value(path, value)
        if reason:
            failures.append(
                PolicyFailure(
                    "volatile-status-provenance",
                    f"{source}: {reason}; keep PR/run/head/branch data in "
                    "GitHub or ephemeral receipts",
                )
            )
    return tuple(failures)


def _git_json(root: Path, revision: str, relative: str) -> Mapping[str, Any] | None:
    process = subprocess.run(
        ["git", "show", f"{revision}:{relative}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if process.returncode != 0:
        if "does not exist" in process.stderr or "exists on disk" in process.stderr:
            return None
        raise PullRequestPolicyError(
            f"unable to read {relative} at {revision}: {process.stderr.strip()}"
        )
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise PullRequestPolicyError(
            f"{relative} at {revision} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(value, Mapping):
        raise PullRequestPolicyError(f"{relative} at {revision} must contain an object")
    return value


def validate_durable_status_sources(
    root: Path, base_revision: str
) -> tuple[PolicyFailure, ...]:
    failures: list[PolicyFailure] = []
    for relative in DURABLE_STATUS_SOURCES:
        path = root / relative
        before = _git_json(root, base_revision, relative)
        if before is None or not path.is_file():
            continue
        try:
            after = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PullRequestPolicyError(f"{relative} is not valid JSON: {exc}") from exc
        if not isinstance(after, Mapping):
            raise PullRequestPolicyError(f"{relative} must contain an object")
        failures.extend(validate_status_changes(before, after, source=relative))
    return tuple(failures)


def _event(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PullRequestPolicyError(f"unable to read pull-request event {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PullRequestPolicyError("pull-request event must contain an object")
    return value


def _event_body_base_and_head(
    event: Mapping[str, Any]
) -> tuple[str, str, str]:
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, Mapping):
        raise PullRequestPolicyError("event has no pull_request object")
    body = pull_request.get("body")
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise PullRequestPolicyError("pull_request.body must be text or null")
    base = pull_request.get("base")
    if not isinstance(base, Mapping) or not isinstance(base.get("sha"), str):
        raise PullRequestPolicyError("pull_request.base.sha is missing")
    head = pull_request.get("head")
    if not isinstance(head, Mapping) or not isinstance(head.get("sha"), str):
        raise PullRequestPolicyError("pull_request.head.sha is missing")
    return body, str(base["sha"]), str(head["sha"])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the current pull-request description and durable status provenance"
    )
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--base")
    parser.add_argument("--template", default=str(TEMPLATE))
    args = parser.parse_args()
    try:
        if not args.event:
            raise PullRequestPolicyError("--event or GITHUB_EVENT_PATH is required")
        body, event_base, event_head = _event_body_base_and_head(
            _event(Path(args.event))
        )
        template = Path(args.template).read_text(encoding="utf-8")
        failures = [*validate_body(body, template)]
        evidence = build_pr_evidence(
            ROOT,
            base_revision=args.base or event_base,
            head_revision=event_head,
        )
        failures.extend(validate_generated_evidence(body, evidence))
        failures.extend(validate_durable_status_sources(ROOT, args.base or event_base))
    except (OSError, PullRequestEvidenceError, PullRequestPolicyError) as exc:
        print(f"::error title=PR description policy setup::{exc}")
        return 2
    if failures:
        for failure in failures:
            print(f"::error title=PR description policy ({failure.code})::{failure.message}")
        print(f"pull-request policy failed with {len(failures)} issue(s)")
        return 1
    print("pull-request description and durable status provenance are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
