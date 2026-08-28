from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import build_opener, HTTPRedirectHandler, Request
import zipfile

from scripts.generated_artifacts import load_manifest
from scripts.generated_finalization_receipt import generated_outputs_fingerprint

try:
    from scripts.source_tree_fingerprint import (
        SOURCE_TREE_FINGERPRINT_ALGORITHM,
        tracked_ref_source_fingerprint,
        tracked_worktree_source_fingerprint,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from source_tree_fingerprint import (  # type: ignore[no-redef]
        SOURCE_TREE_FINGERPRINT_ALGORITHM,
        tracked_ref_source_fingerprint,
        tracked_worktree_source_fingerprint,
    )


ROOT = Path(__file__).resolve().parents[1]
RECEIPT_SCHEMA_VERSION = 3
RECEIPT_FILENAME = "certification-receipt.json"
CERTIFICATION_MODES = frozenset({"executed", "reused"})
CERTIFICATION_PROFILES = frozenset(
    {"complete", "affected", "governance", "recovery"}
)
ACTIVE_WORKFLOW_RUN_STATUSES = frozenset(
    {"pending", "queued", "requested", "waiting", "in_progress"}
)
QUEUED_RUN_MATERIALIZATION_GRACE_SECONDS = 300
FULL_REQUIRED_CHECK_SUITE = frozenset(
    {
        "browser",
        "generated",
        "package",
        "plan",
        "python",
        "windows_certification",
    }
)
# Compatibility name for callers that mean the complete source gate.
REQUIRED_CHECK_SUITE = FULL_REQUIRED_CHECK_SUITE
GOVERNANCE_REQUIRED_CHECK_SUITE = frozenset({"generated", "plan"})
RECOVERY_REQUIRED_CHECK_SUITE = frozenset({"generated", "plan", "python"})
ALLOWED_REQUIRED_CHECK_SUITES = frozenset(
    {
        FULL_REQUIRED_CHECK_SUITE,
        GOVERNANCE_REQUIRED_CHECK_SUITE,
        RECOVERY_REQUIRED_CHECK_SUITE,
    }
)
PROFILE_REQUIRED_CHECK_SUITE = {
    "complete": FULL_REQUIRED_CHECK_SUITE,
    "affected": FULL_REQUIRED_CHECK_SUITE,
    "governance": GOVERNANCE_REQUIRED_CHECK_SUITE,
    "recovery": RECOVERY_REQUIRED_CHECK_SUITE,
}
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "repository",
        "pull_request",
        "exact_head_sha",
        "workflow_run_id",
        "evidence_workflow_run_id",
        "certification_mode",
        "certification_profile",
        "workflow_name",
        "check_suite",
        "source_tree_fingerprint_algorithm",
        "source_tree_fingerprint",
        "generated_outputs_fingerprint",
    }
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class CertificationReceiptError(ValueError):
    """An exact-head certification receipt is missing, stale, or malformed."""


def _positive_integer(value: Any, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise CertificationReceiptError(f"{field} must be a positive integer")
    return value


def canonical_check_suite(
    needs: Mapping[str, Any],
    required_jobs: frozenset[str] | None = None,
) -> dict[str, str]:
    required = FULL_REQUIRED_CHECK_SUITE if required_jobs is None else required_jobs
    if required not in ALLOWED_REQUIRED_CHECK_SUITES:
        raise CertificationReceiptError(
            "Certification selected an unsupported required-check profile"
        )
    if not isinstance(needs, Mapping) or not required.issubset(needs):
        raise CertificationReceiptError(
            "Certification check suite must contain every required PR gate"
        )
    result: dict[str, str] = {}
    for name in sorted(required):
        details = needs.get(name)
        if not isinstance(details, Mapping) or details.get("result") != "success":
            raise CertificationReceiptError(
                f"Certification gate {name!r} did not succeed"
            )
        result[name] = "success"
    return result


@dataclass(frozen=True, slots=True)
class CertificationReceipt:
    repository: str
    pull_request: int
    exact_head_sha: str
    workflow_run_id: int
    check_suite: tuple[tuple[str, str], ...]
    source_tree_fingerprint: str
    generated_outputs_fingerprint: str
    workflow_name: str = "PR"
    evidence_workflow_run_id: int | None = None
    certification_mode: str = "executed"
    certification_profile: str = "affected"

    def __post_init__(self) -> None:
        if (
            type(self.repository) is not str
            or self.repository.count("/") != 1
            or not all(self.repository.split("/"))
        ):
            raise CertificationReceiptError(
                "repository must be an owner/name coordinate"
            )
        _positive_integer(self.pull_request, field="pull_request")
        _positive_integer(self.workflow_run_id, field="workflow_run_id")
        evidence_run = (
            self.workflow_run_id
            if self.evidence_workflow_run_id is None
            else self.evidence_workflow_run_id
        )
        _positive_integer(
            evidence_run,
            field="evidence_workflow_run_id",
        )
        if self.certification_mode not in CERTIFICATION_MODES:
            raise CertificationReceiptError(
                "certification_mode must be executed or reused"
            )
        if (
            self.certification_mode == "executed"
            and evidence_run != self.workflow_run_id
        ):
            raise CertificationReceiptError(
                "Executed certification evidence must come from its workflow run"
            )
        if (
            self.certification_mode == "reused"
            and evidence_run == self.workflow_run_id
        ):
            raise CertificationReceiptError(
                "Reused certification must identify an earlier evidence run"
            )
        if self.certification_profile not in CERTIFICATION_PROFILES:
            raise CertificationReceiptError(
                "certification_profile is unsupported"
            )
        if type(self.exact_head_sha) is not str or not _SHA.fullmatch(
            self.exact_head_sha
        ):
            raise CertificationReceiptError(
                "exact_head_sha must be a lowercase full Git SHA"
            )
        if self.workflow_name != "PR":
            raise CertificationReceiptError("workflow_name must identify PR CI")
        suite = dict(self.check_suite)
        if len(suite) != len(self.check_suite):
            raise CertificationReceiptError("check_suite contains duplicate gates")
        canonical_check_suite(
            {name: {"result": result} for name, result in suite.items()},
            frozenset(suite),
        )
        if frozenset(suite) != PROFILE_REQUIRED_CHECK_SUITE[
            self.certification_profile
        ]:
            raise CertificationReceiptError(
                "certification_profile does not match its required checks"
            )
        if (
            type(self.source_tree_fingerprint) is not str
            or not _FINGERPRINT.fullmatch(self.source_tree_fingerprint)
        ):
            raise CertificationReceiptError(
                "source_tree_fingerprint must be a lowercase SHA-256 value"
            )
        if (
            type(self.generated_outputs_fingerprint) is not str
            or not _FINGERPRINT.fullmatch(self.generated_outputs_fingerprint)
        ):
            raise CertificationReceiptError(
                "generated_outputs_fingerprint must be a lowercase SHA-256 value"
            )
        object.__setattr__(self, "check_suite", tuple(sorted(suite.items())))
        object.__setattr__(self, "evidence_workflow_run_id", evidence_run)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "repository": self.repository,
            "pull_request": self.pull_request,
            "exact_head_sha": self.exact_head_sha,
            "workflow_run_id": self.workflow_run_id,
            "evidence_workflow_run_id": self.evidence_workflow_run_id,
            "certification_mode": self.certification_mode,
            "certification_profile": self.certification_profile,
            "workflow_name": self.workflow_name,
            "check_suite": dict(self.check_suite),
            "source_tree_fingerprint_algorithm": (
                SOURCE_TREE_FINGERPRINT_ALGORITHM
            ),
            "source_tree_fingerprint": self.source_tree_fingerprint,
            "generated_outputs_fingerprint": self.generated_outputs_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CertificationReceipt":
        if not isinstance(value, Mapping) or set(value) != _RECEIPT_FIELDS:
            raise CertificationReceiptError(
                "Certification receipt fields are incomplete or unknown"
            )
        if value.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            raise CertificationReceiptError(
                "Certification receipt schema version is unsupported"
            )
        if (
            value.get("source_tree_fingerprint_algorithm")
            != SOURCE_TREE_FINGERPRINT_ALGORITHM
        ):
            raise CertificationReceiptError(
                "Certification receipt fingerprint algorithm is unsupported"
            )
        suite = value.get("check_suite")
        if not isinstance(suite, Mapping):
            raise CertificationReceiptError("check_suite must be an object")
        return cls(
            repository=value.get("repository"),
            pull_request=value.get("pull_request"),
            exact_head_sha=value.get("exact_head_sha"),
            workflow_run_id=value.get("workflow_run_id"),
            evidence_workflow_run_id=value.get("evidence_workflow_run_id"),
            certification_mode=value.get("certification_mode"),
            certification_profile=value.get("certification_profile"),
            workflow_name=value.get("workflow_name"),
            check_suite=tuple(suite.items()),
            source_tree_fingerprint=value.get("source_tree_fingerprint"),
            generated_outputs_fingerprint=value.get(
                "generated_outputs_fingerprint"
            ),
        )


def build_receipt(
    *,
    repository: str,
    pull_request: int,
    exact_head_sha: str,
    workflow_run_id: int,
    needs: Mapping[str, Any],
    required_jobs: frozenset[str] | None = None,
    certification_profile: str = "affected",
    root: Path = ROOT,
) -> CertificationReceipt:
    suite = canonical_check_suite(needs, required_jobs)
    if certification_profile not in PROFILE_REQUIRED_CHECK_SUITE:
        raise CertificationReceiptError("Certification profile is unsupported")
    if frozenset(suite) != PROFILE_REQUIRED_CHECK_SUITE[certification_profile]:
        raise CertificationReceiptError(
            "Certification profile does not match its required checks"
        )
    return CertificationReceipt(
        repository=repository,
        pull_request=pull_request,
        exact_head_sha=exact_head_sha,
        workflow_run_id=workflow_run_id,
        check_suite=tuple(suite.items()),
        certification_profile=certification_profile,
        source_tree_fingerprint=tracked_ref_source_fingerprint(
            root, exact_head_sha
        ),
        generated_outputs_fingerprint=generated_outputs_fingerprint(
            load_manifest(root / "platform/generated-artifacts.json", root=root),
            root=root,
        ),
    )


def build_reused_receipt(
    prior: CertificationReceipt,
    *,
    workflow_run_id: int,
) -> CertificationReceipt:
    """Publish honest provenance for unchanged-head reused matrix evidence."""

    if not isinstance(prior, CertificationReceipt):
        raise CertificationReceiptError(
            "Reused certification requires a validated prior receipt"
        )
    if workflow_run_id == prior.workflow_run_id:
        raise CertificationReceiptError(
            "Reused certification requires a new publication workflow"
        )
    return CertificationReceipt(
        repository=prior.repository,
        pull_request=prior.pull_request,
        exact_head_sha=prior.exact_head_sha,
        workflow_run_id=workflow_run_id,
        evidence_workflow_run_id=prior.evidence_workflow_run_id,
        certification_mode="reused",
        certification_profile=prior.certification_profile,
        check_suite=prior.check_suite,
        source_tree_fingerprint=prior.source_tree_fingerprint,
        generated_outputs_fingerprint=prior.generated_outputs_fingerprint,
    )


def validate_receipt(
    receipt: CertificationReceipt,
    *,
    repository: str,
    pull_request: int,
    exact_head_sha: str,
    workflow_run_id: int,
    evaluated_source_tree_fingerprint: str,
    evaluated_generated_outputs_fingerprint: str,
) -> None:
    expected = {
        "repository": repository,
        "pull_request": pull_request,
        "exact_head_sha": exact_head_sha,
        "workflow_run_id": workflow_run_id,
    }
    for field, value in expected.items():
        if getattr(receipt, field) != value:
            raise CertificationReceiptError(
                f"Certification receipt {field} does not match GitHub"
            )
    if receipt.source_tree_fingerprint != evaluated_source_tree_fingerprint:
        raise CertificationReceiptError(
            "Current main source tree is not equivalent to the certified PR head"
        )
    if (
        receipt.generated_outputs_fingerprint
        != evaluated_generated_outputs_fingerprint
    ):
        raise CertificationReceiptError(
            "Current generated outputs are not equivalent to the certified PR head"
        )


def select_merged_pull_request(value: Any, *, merge_sha: str) -> Mapping[str, Any]:
    if not isinstance(value, list):
        raise CertificationReceiptError(
            "GitHub associated-pull-request response is malformed"
        )
    candidates: dict[int, Mapping[str, Any]] = {}
    for row in value:
        if (
            not isinstance(row, Mapping)
            or row.get("state") != "closed"
            or not row.get("merged_at")
            or row.get("merge_commit_sha") != merge_sha
        ):
            continue
        number = row.get("number")
        if type(number) is not int or number <= 0:
            raise CertificationReceiptError(
                "Merged pull request has no valid number"
            )
        existing = candidates.setdefault(number, row)
        existing_head = existing.get("head")
        candidate_head = row.get("head")
        if (
            isinstance(existing_head, Mapping)
            and isinstance(candidate_head, Mapping)
            and existing_head.get("sha") != candidate_head.get("sha")
        ):
            raise CertificationReceiptError(
                "Duplicate merged pull request payloads disagree on head SHA"
            )
    if len(candidates) != 1:
        raise CertificationReceiptError(
            "Current main commit must identify exactly one merged pull request"
        )
    return next(iter(candidates.values()))


def successful_pr_runs(value: Any, *, exact_head_sha: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Mapping) or not isinstance(
        value.get("workflow_runs"), list
    ):
        raise CertificationReceiptError("GitHub workflow-run response is malformed")
    candidates = [
        row
        for row in value["workflow_runs"]
        if isinstance(row, Mapping)
        and row.get("event") == "pull_request"
        and row.get("head_sha") == exact_head_sha
        and row.get("name") == "PR"
        and row.get("path") == ".github/workflows/ci.yml"
        and type(row.get("id")) is int
    ]
    return tuple(sorted(candidates, key=lambda row: int(row["id"]), reverse=True))


def _github_timestamp_seconds(value: Any, *, field: str) -> float:
    if type(value) is not str:
        raise CertificationReceiptError(f"{field} must be a GitHub timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CertificationReceiptError(
            f"{field} must be a GitHub timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise CertificationReceiptError(f"{field} must include a timezone")
    return parsed.timestamp()


def _workflow_run_has_materialized_jobs(value: Any) -> bool:
    if (
        not isinstance(value, Mapping)
        or type(value.get("total_count")) is not int
        or value["total_count"] < 0
        or not isinstance(value.get("jobs"), list)
    ):
        raise CertificationReceiptError("GitHub workflow-job response is malformed")
    return value["total_count"] > 0


def _workflow_run_has_failed_job(value: Any) -> bool:
    if (
        not isinstance(value, Mapping)
        or type(value.get("total_count")) is not int
        or value["total_count"] < 0
        or not isinstance(value.get("jobs"), list)
        or any(not isinstance(job, Mapping) for job in value["jobs"])
    ):
        raise CertificationReceiptError("GitHub workflow-job response is malformed")
    return any(job.get("conclusion") == "failure" for job in value["jobs"])


def _previous_run_can_still_certify(
    run: Mapping[str, Any],
    *,
    current_workflow_run_id: int,
    api: str,
    token: str,
) -> bool:
    run_id = int(run["id"])
    status = run.get("status")
    if (
        run_id >= current_workflow_run_id
        or status not in ACTIVE_WORKFLOW_RUN_STATUSES
    ):
        return False
    if status == "in_progress":
        jobs = _github_json(
            f"{api}/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
            token,
        )
        if _workflow_run_has_failed_job(jobs):
            return False
    if status != "queued":
        return True
    jobs = _github_json(
        f"{api}/actions/runs/{run_id}/jobs?per_page=1",
        token,
    )
    if _workflow_run_has_materialized_jobs(jobs):
        return True
    updated_at = _github_timestamp_seconds(
        run.get("updated_at"),
        field="workflow run updated_at",
    )
    return (
        time.time() - updated_at
        <= QUEUED_RUN_MATERIALIZATION_GRACE_SECONDS
    )


def select_receipt_artifact(value: Any, *, workflow_run_id: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not isinstance(value.get("artifacts"), list):
        raise CertificationReceiptError("GitHub artifact response is malformed")
    expected_name = f"exact-head-certification-{workflow_run_id}"
    candidates = [
        row
        for row in value["artifacts"]
        if isinstance(row, Mapping)
        and row.get("name") == expected_name
        and row.get("expired") is False
        and isinstance(row.get("archive_download_url"), str)
    ]
    if len(candidates) != 1:
        raise CertificationReceiptError(
            "Successful PR run has no unique live certification receipt"
        )
    return candidates[0]


def receipt_from_archive(raw: bytes) -> CertificationReceipt:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if names != [RECEIPT_FILENAME]:
                raise CertificationReceiptError(
                    "Certification artifact must contain only the canonical receipt"
                )
            value = json.loads(archive.read(RECEIPT_FILENAME).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise CertificationReceiptError(
            "Certification artifact is malformed"
        ) from exc
    return CertificationReceipt.from_dict(value)


class _CertificationRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        redirected = super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )
        if (
            redirected is not None
            and urlparse(request.full_url).netloc
            != urlparse(new_url).netloc
        ):
            redirected.remove_header("Authorization")
        return redirected


def _github_request(url: str, token: str) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "quorune-certification",
        },
    )
    try:
        with build_opener(_CertificationRedirectHandler()).open(
            request, timeout=30
        ) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise CertificationReceiptError(
            "GitHub certification evidence could not be retrieved"
        ) from exc


def _github_json(url: str, token: str) -> Any:
    try:
        return json.loads(_github_request(url, token).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CertificationReceiptError("GitHub returned malformed JSON") from exc


def find_previous_pr_certification(
    *,
    repository: str,
    pull_request: int,
    exact_head_sha: str,
    current_workflow_run_id: int,
    token: str,
    root: Path = ROOT,
) -> CertificationReceipt:
    """Find validated prior full-matrix evidence for this unchanged PR head."""

    if type(repository) is not str or repository.count("/") != 1:
        raise CertificationReceiptError(
            "repository must be an owner/name coordinate"
        )
    _positive_integer(pull_request, field="pull_request")
    _positive_integer(
        current_workflow_run_id,
        field="current_workflow_run_id",
    )
    if type(exact_head_sha) is not str or not _SHA.fullmatch(exact_head_sha):
        raise CertificationReceiptError(
            "exact_head_sha must be a lowercase full Git SHA"
        )
    api = f"https://api.github.com/repos/{repository}"
    runs = successful_pr_runs(
        _github_json(
            f"{api}/actions/workflows/ci.yml/runs?event=pull_request"
            f"&head_sha={quote(exact_head_sha)}&per_page=100",
            token,
        ),
        exact_head_sha=exact_head_sha,
    )
    fingerprint = tracked_worktree_source_fingerprint(root)
    generated_fingerprint = generated_outputs_fingerprint(
        load_manifest(root / "platform/generated-artifacts.json", root=root),
        root=root,
    )
    last_error: CertificationReceiptError | None = None
    for run in runs:
        run_id = int(run["id"])
        if run_id >= current_workflow_run_id:
            continue
        try:
            artifact = select_receipt_artifact(
                _github_json(
                    f"{api}/actions/runs/{run_id}/artifacts?per_page=100",
                    token,
                ),
                workflow_run_id=run_id,
            )
            receipt = receipt_from_archive(
                _github_request(str(artifact["archive_download_url"]), token)
            )
            validate_receipt(
                receipt,
                repository=repository,
                pull_request=pull_request,
                exact_head_sha=exact_head_sha,
                workflow_run_id=run_id,
                evaluated_source_tree_fingerprint=fingerprint,
                evaluated_generated_outputs_fingerprint=generated_fingerprint,
            )
            return receipt
        except CertificationReceiptError as exc:
            last_error = exc
    raise last_error or CertificationReceiptError(
        "Pull request has no reusable exact-head certification receipt"
    )


def wait_for_previous_pr_certification(
    *,
    repository: str,
    pull_request: int,
    exact_head_sha: str,
    current_workflow_run_id: int,
    token: str,
    wait_seconds: int,
    poll_seconds: int = 15,
    root: Path = ROOT,
) -> CertificationReceipt:
    """Wait for an older unchanged-head run instead of duplicating its matrix."""

    if wait_seconds < 0:
        raise CertificationReceiptError("wait_seconds must not be negative")
    if poll_seconds <= 0:
        raise CertificationReceiptError("poll_seconds must be positive")
    deadline = time.monotonic() + wait_seconds
    api = f"https://api.github.com/repos/{repository}"
    runs_url = (
        f"{api}/actions/workflows/ci.yml/runs?event=pull_request"
        f"&head_sha={quote(exact_head_sha)}&per_page=100"
    )
    while True:
        try:
            return find_previous_pr_certification(
                repository=repository,
                pull_request=pull_request,
                exact_head_sha=exact_head_sha,
                current_workflow_run_id=current_workflow_run_id,
                token=token,
                root=root,
            )
        except CertificationReceiptError as exc:
            last_error = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise last_error
        runs = successful_pr_runs(
            _github_json(runs_url, token),
            exact_head_sha=exact_head_sha,
        )
        if not any(
            _previous_run_can_still_certify(
                run,
                current_workflow_run_id=current_workflow_run_id,
                api=api,
                token=token,
            )
            for run in runs
        ):
            raise last_error
        time.sleep(min(float(poll_seconds), remaining))


def verify_main_certification(
    *,
    repository: str,
    merge_sha: str,
    token: str,
    root: Path = ROOT,
) -> CertificationReceipt:
    if not _SHA.fullmatch(merge_sha):
        raise CertificationReceiptError("merge_sha must be a lowercase full Git SHA")
    api = f"https://api.github.com/repos/{repository}"
    associated = _github_json(
        f"{api}/commits/{merge_sha}/pulls",
        token,
    )
    recent = _github_json(
        f"{api}/pulls?state=closed&sort=updated"
        "&direction=desc&per_page=100",
        token,
    )
    if not isinstance(associated, list) or not isinstance(recent, list):
        raise CertificationReceiptError(
            "GitHub merged-pull-request responses are malformed"
        )
    pull_request = select_merged_pull_request(
        [*associated, *recent],
        merge_sha=merge_sha,
    )
    pull_request_number = _positive_integer(
        pull_request.get("number"), field="pull request number"
    )
    head = pull_request.get("head")
    exact_head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if type(exact_head_sha) is not str or not _SHA.fullmatch(exact_head_sha):
        raise CertificationReceiptError(
            "Merged pull request has no exact head SHA"
        )
    last_error: CertificationReceiptError | None = None
    for attempt in range(5):
        runs = successful_pr_runs(
            _github_json(
                f"{api}/actions/workflows/ci.yml/runs?event=pull_request"
                f"&head_sha={quote(exact_head_sha)}&per_page=100",
                token,
            ),
            exact_head_sha=exact_head_sha,
        )
        if not runs:
            last_error = CertificationReceiptError(
                "Merged pull request has no exact-head PR workflow"
            )
        for run in runs:
            run_id = int(run["id"])
            try:
                artifact = select_receipt_artifact(
                    _github_json(
                        f"{api}/actions/runs/{run_id}/artifacts?per_page=100",
                        token,
                    ),
                    workflow_run_id=run_id,
                )
                receipt = receipt_from_archive(
                    _github_request(str(artifact["archive_download_url"]), token)
                )
                validate_receipt(
                    receipt,
                    repository=repository,
                    pull_request=pull_request_number,
                    exact_head_sha=exact_head_sha,
                    workflow_run_id=run_id,
                    evaluated_source_tree_fingerprint=(
                        tracked_worktree_source_fingerprint(root)
                    ),
                    evaluated_generated_outputs_fingerprint=(
                        generated_outputs_fingerprint(
                            load_manifest(
                                root / "platform/generated-artifacts.json",
                                root=root,
                            ),
                            root=root,
                        )
                    ),
                )
                return receipt
            except CertificationReceiptError as exc:
                last_error = exc
        if attempt < 4:
            time.sleep(2)
    raise last_error or CertificationReceiptError(
        "No successful PR run supplied a valid certification receipt"
    )


def _read_needs(raw: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CertificationReceiptError("CI needs JSON is malformed") from exc
    if not isinstance(value, Mapping):
        raise CertificationReceiptError("CI needs JSON must be an object")
    return value


def _write_receipt(receipt: CertificationReceipt, output: str | Path) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_github_output(
    path: str | Path,
    *,
    reusable: bool,
    key: str = "reuse_certification",
) -> None:
    with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{key}={str(reusable).lower()}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--repository", required=True)
    create.add_argument("--pull-request", type=int, required=True)
    create.add_argument("--exact-head-sha", required=True)
    create.add_argument("--workflow-run-id", type=int, required=True)
    create.add_argument("--needs-json", required=True)
    create.add_argument("--required-jobs-json", required=True)
    create.add_argument(
        "--certification-profile",
        choices=sorted(CERTIFICATION_PROFILES),
        required=True,
    )
    create.add_argument("--output", required=True)
    can_reuse = subparsers.add_parser("can-reuse-pr")
    can_reuse.add_argument("--repository", required=True)
    can_reuse.add_argument("--pull-request", type=int, required=True)
    can_reuse.add_argument("--exact-head-sha", required=True)
    can_reuse.add_argument("--workflow-run-id", type=int, required=True)
    can_reuse.add_argument("--event-action", required=True)
    can_reuse.add_argument("--wait-seconds", type=int, default=0)
    can_reuse.add_argument("--github-output", required=True)
    reuse = subparsers.add_parser("reuse-pr")
    reuse.add_argument("--repository", required=True)
    reuse.add_argument("--pull-request", type=int, required=True)
    reuse.add_argument("--exact-head-sha", required=True)
    reuse.add_argument("--workflow-run-id", type=int, required=True)
    reuse.add_argument("--output", required=True)
    verify = subparsers.add_parser("verify-main")
    verify.add_argument("--repository", required=True)
    verify.add_argument("--merge-sha", required=True)
    broad_reuse = subparsers.add_parser("can-reuse-main-broad")
    broad_reuse.add_argument("--repository", required=True)
    broad_reuse.add_argument("--merge-sha", required=True)
    broad_reuse.add_argument("--github-output", required=True)
    args = parser.parse_args(argv)
    try:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if args.operation != "create" and not token:
            raise CertificationReceiptError(
                "GH_TOKEN or GITHUB_TOKEN is required"
            )
        if args.operation == "create":
            required_value = json.loads(args.required_jobs_json)
            if (
                not isinstance(required_value, list)
                or not required_value
                or any(not isinstance(item, str) for item in required_value)
                or len(required_value) != len(set(required_value))
            ):
                raise CertificationReceiptError(
                    "required-jobs-json must contain a list of strings"
                )
            receipt = build_receipt(
                repository=args.repository,
                pull_request=args.pull_request,
                exact_head_sha=args.exact_head_sha,
                workflow_run_id=args.workflow_run_id,
                needs=_read_needs(args.needs_json),
                required_jobs=frozenset(required_value),
                certification_profile=args.certification_profile,
            )
            _write_receipt(receipt, args.output)
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        if args.operation == "can-reuse-pr":
            reusable = False
            reason = "source-changing or non-edit event"
            if args.event_action == "edited":
                try:
                    wait_for_previous_pr_certification(
                        repository=args.repository,
                        pull_request=args.pull_request,
                        exact_head_sha=args.exact_head_sha,
                        current_workflow_run_id=args.workflow_run_id,
                        token=token,
                        wait_seconds=args.wait_seconds,
                    )
                    reusable = True
                    reason = "validated prior exact-head certification"
                except CertificationReceiptError as exc:
                    reason = str(exc)
            _write_github_output(
                args.github_output,
                reusable=reusable,
            )
            print(
                json.dumps(
                    {
                        "reuse_certification": reusable,
                        "reason": reason,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.operation == "reuse-pr":
            prior = find_previous_pr_certification(
                repository=args.repository,
                pull_request=args.pull_request,
                exact_head_sha=args.exact_head_sha,
                current_workflow_run_id=args.workflow_run_id,
                token=token,
            )
            receipt = build_reused_receipt(
                prior,
                workflow_run_id=args.workflow_run_id,
            )
            _write_receipt(receipt, args.output)
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        if args.operation == "can-reuse-main-broad":
            reusable = False
            reason = "no valid complete PR certification"
            try:
                receipt = verify_main_certification(
                    repository=args.repository,
                    merge_sha=args.merge_sha,
                    token=token,
                )
                reusable = receipt.certification_profile == "complete"
                reason = (
                    "verified complete source-tree certification"
                    if reusable
                    else f"receipt profile is {receipt.certification_profile}"
                )
            except CertificationReceiptError as exc:
                reason = str(exc)
            _write_github_output(
                args.github_output,
                reusable=reusable,
                key="reuse_main_broad",
            )
            print(
                json.dumps(
                    {"reuse_main_broad": reusable, "reason": reason},
                    sort_keys=True,
                )
            )
            return 0
        receipt = verify_main_certification(
            repository=args.repository,
            merge_sha=args.merge_sha,
            token=token,
        )
        print(json.dumps(receipt.to_dict(), sort_keys=True))
        return 0
    except (CertificationReceiptError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
