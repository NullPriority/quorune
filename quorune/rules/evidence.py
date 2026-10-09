from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from ..util import stable_json


CAPABILITY_EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_CLASSES = {
    "positive",
    "negative",
    "interaction",
    "multiplayer",
    "privacy",
    "replay",
    "rollback",
    "property",
    "fuzz",
    "mutation",
}
MINIMUM_TRUSTED_EVIDENCE = frozenset(
    {"positive", "negative", "replay", "mutation"}
)
DEFAULT_CAPABILITY_EVIDENCE = (
    Path(__file__).resolve().with_name("capability-evidence.json")
)
_INDEX_FIELDS = {
    "schema_version",
    "registry_fingerprint",
    "declaration_source_fingerprint",
    "declarations",
    "fingerprint",
}
_DECLARATION_FIELDS = {
    "capability_id",
    "evidence_class",
    "test_id",
    "official_rule_ids",
    "supported_profiles",
    "applicability_note",
}
_TEST_ID = re.compile(
    r"^tests\.test_[a-z0-9_]+(?:\.[A-Za-z_][A-Za-z0-9_]*)?\.test_[a-z0-9_]+$"
)


class CapabilityEvidenceError(ValueError):
    """A capability-evidence artifact is stale, ambiguous, or malformed."""


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _strings(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise CapabilityEvidenceError(
            f"{field} must be a list of nonempty strings"
        )
    result = tuple(str(item) for item in value)
    if len(result) != len(set(result)):
        raise CapabilityEvidenceError(f"{field} must contain unique values")
    return result


def _exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    *,
    field: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if unknown:
            detail.append("unknown " + ", ".join(unknown))
        raise CapabilityEvidenceError(f"{field} fields: {'; '.join(detail)}")


def validate_capability_evidence_index(
    value: Mapping[str, Any],
    *,
    registry: Any,
) -> str:
    """Validate a generated index without importing or inspecting tests.

    Test discovery belongs to the generator/CI boundary. Runtime validation
    binds the already-validated index to the exact capability registry and
    proves that every trusted capability has its declared evidence classes.
    """

    raw = json.loads(json.dumps(dict(value)))
    _exact_fields(raw, _INDEX_FIELDS, field="capability evidence index")
    if raw.get("schema_version") != CAPABILITY_EVIDENCE_SCHEMA_VERSION:
        raise CapabilityEvidenceError(
            "Unsupported capability evidence schema_version"
        )
    if raw.get("registry_fingerprint") != registry.fingerprint:
        raise CapabilityEvidenceError(
            "Capability evidence registry fingerprint is stale"
        )
    declarations = raw.get("declarations")
    if not isinstance(declarations, list):
        raise CapabilityEvidenceError("declarations must be a list")
    known_profiles = set(registry.profiles)
    capabilities = {row["id"]: row for row in registry.capabilities()}
    evidence_by_capability: dict[str, set[str]] = {}
    profiles_by_capability_evidence: dict[
        tuple[str, str], set[str]
    ] = {}
    identities: set[tuple[str, str, str]] = set()
    normalized: list[dict[str, Any]] = []
    for index, candidate in enumerate(declarations):
        if not isinstance(candidate, Mapping):
            raise CapabilityEvidenceError(
                f"declarations[{index}] must be an object"
            )
        row = dict(candidate)
        _exact_fields(
            row, _DECLARATION_FIELDS, field=f"declarations[{index}]"
        )
        capability_id = str(row.get("capability_id") or "")
        capability = capabilities.get(capability_id)
        if capability is None:
            raise CapabilityEvidenceError(
                f"Unknown capability evidence target: {capability_id}"
            )
        evidence_class = str(row.get("evidence_class") or "")
        if evidence_class not in EVIDENCE_CLASSES:
            raise CapabilityEvidenceError(
                f"Unknown evidence class: {evidence_class}"
            )
        test_id = str(row.get("test_id") or "")
        if _TEST_ID.fullmatch(test_id) is None:
            raise CapabilityEvidenceError(
                f"Invalid fully qualified test_id: {test_id!r}"
            )
        identity = (capability_id, evidence_class, test_id)
        if identity in identities:
            raise CapabilityEvidenceError(
                "Duplicate capability evidence declaration: "
                + ":".join(identity)
            )
        identities.add(identity)
        rules = _strings(
            row.get("official_rule_ids"),
            field=f"declarations[{index}].official_rule_ids",
        )
        profiles = _strings(
            row.get("supported_profiles"),
            field=f"declarations[{index}].supported_profiles",
        )
        if not rules:
            raise CapabilityEvidenceError(
                f"declarations[{index}] requires official_rule_ids"
            )
        if set(rules) != set(capability["official_rules"]):
            raise CapabilityEvidenceError(
                f"{test_id} must cite the current official rules for "
                f"{capability_id}"
            )
        if not profiles or set(profiles) - known_profiles or set(
            profiles
        ) - set(capability["supported_profiles"]):
            raise CapabilityEvidenceError(
                f"{test_id} cites unsupported profiles for {capability_id}"
            )
        note = str(row.get("applicability_note") or "").strip()
        if not note:
            raise CapabilityEvidenceError(
                f"{test_id} requires an applicability_note"
            )
        normalized.append(
            {
                "capability_id": capability_id,
                "evidence_class": evidence_class,
                "test_id": test_id,
                "official_rule_ids": list(rules),
                "supported_profiles": list(profiles),
                "applicability_note": note,
            }
        )
        evidence_by_capability.setdefault(capability_id, set()).add(
            evidence_class
        )
        profiles_by_capability_evidence.setdefault(
            (capability_id, evidence_class), set()
        ).update(profiles)
    expected_order = sorted(
        normalized,
        key=lambda row: (
            row["capability_id"],
            row["evidence_class"],
            row["test_id"],
        ),
    )
    if declarations != expected_order:
        raise CapabilityEvidenceError(
            "Capability evidence declarations are not canonically ordered"
        )
    for capability in capabilities.values():
        if capability["status"] != "trusted":
            continue
        capability_id = capability["id"]
        supplied = evidence_by_capability.get(capability_id, set())
        required = (
            set(capability["required_evidence"])
            | MINIMUM_TRUSTED_EVIDENCE
        )
        missing = required - supplied
        if missing:
            raise CapabilityEvidenceError(
                f"Trusted {capability_id} lacks explicit evidence: "
                + ", ".join(sorted(missing))
            )
        expected_profiles = set(capability["supported_profiles"])
        for evidence_class in sorted(required):
            covered_profiles = profiles_by_capability_evidence.get(
                (capability_id, evidence_class), set()
            )
            missing_profiles = expected_profiles - covered_profiles
            if missing_profiles:
                raise CapabilityEvidenceError(
                    f"Trusted {capability_id} {evidence_class} evidence "
                    "does not cover supported profiles: "
                    + ", ".join(sorted(missing_profiles))
                )
    payload = dict(raw)
    fingerprint = str(payload.pop("fingerprint") or "")
    if fingerprint != _hash(payload):
        raise CapabilityEvidenceError(
            "Capability evidence fingerprint does not match"
        )
    return fingerprint


def load_capability_evidence_index(
    path: str | Path = DEFAULT_CAPABILITY_EVIDENCE,
    *,
    registry: Any,
) -> tuple[dict[str, Any], str]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    fingerprint = validate_capability_evidence_index(
        value, registry=registry
    )
    return value, fingerprint


def capability_evidence_fingerprint(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("fingerprint", None)
    return _hash(payload)


_VALIDATION_REGISTRY: ContextVar[dict[str, Any] | None] = ContextVar(
    "validation_registry", default=None
)


@contextmanager
def _capability_validation_snapshot():
    """Share validated read-only inputs only within one component batch."""
    if _VALIDATION_REGISTRY.get() is not None:
        yield
        return
    token = _VALIDATION_REGISTRY.set({})
    try:
        yield
    finally:
        _VALIDATION_REGISTRY.reset(token)


def _load_verified_default_capability_registry(registry_path):
    from .capabilities import CapabilityRegistry

    batch = _VALIDATION_REGISTRY.get()
    if batch is not None and "registry" in batch:
        return batch["registry"]
    registry = CapabilityRegistry.from_path(registry_path)
    _, fingerprint = load_capability_evidence_index(registry=registry)
    registry.mark_evidence_verified(fingerprint)
    if batch is not None:
        batch["registry"] = registry
    return registry
