from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
from threading import RLock
from typing import Any, Mapping

from ..util import stable_json
from . import evidence_storage
from .evidence_storage import read_evidence_document


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


def _verified_strings(
    value: Any, *, field: str, verified: dict[tuple[str, ...], tuple[str, ...]],
) -> tuple[str, ...]:
    # Per-index cache of validated immutable string lists from decoded JSON.
    if isinstance(value, list):
        try:
            return verified[tuple(value)]
        except (KeyError, TypeError):
            pass
    result = _strings(value, field=field)
    verified[result] = result
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
    if isinstance(raw.get("schema_version"), bool) or raw.get("schema_version") != CAPABILITY_EVIDENCE_SCHEMA_VERSION:
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
    verified_strings: dict[tuple[str, ...], tuple[str, ...]] = {}
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(declarations):
        if not isinstance(row, Mapping):
            raise CapabilityEvidenceError(
                f"declarations[{index}] must be an object"
            )
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
        rules = _verified_strings(
            row.get("official_rule_ids"),
            field=f"declarations[{index}].official_rule_ids",
            verified=verified_strings,
        )
        profiles = _verified_strings(
            row.get("supported_profiles"),
            field=f"declarations[{index}].supported_profiles",
            verified=verified_strings,
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
    fingerprint = str(raw.pop("fingerprint") or "")
    if fingerprint != _hash(raw):
        raise CapabilityEvidenceError(
            "Capability evidence fingerprint does not match"
        )
    return fingerprint


def load_capability_evidence_index(
    path: str | Path = DEFAULT_CAPABILITY_EVIDENCE,
    *,
    registry: Any,
) -> tuple[dict[str, Any], str]:
    value = read_evidence_document(path)
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
    """Share validated read-only inputs within one synchronous validation operation."""
    if _VALIDATION_REGISTRY.get() is not None:
        yield
        return
    token = _VALIDATION_REGISTRY.set({})
    try:
        yield
    finally:
        _VALIDATION_REGISTRY.reset(token)


class _VerifiedEvidenceFingerprintCache:
    """Bound successful validation to exact current bytes and policy, never mutable objects."""

    def __init__(self, capacity=4):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("Cache capacity must be a positive integer")
        self.capacity = capacity
        self._entries = OrderedDict()
        self._lock = RLock()

    def fingerprint(self, registry, path):
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        functions = (
            validate_capability_evidence_index, _exact_fields, _strings, _hash,
            stable_json, evidence_storage.unpack_evidence_document,
            json.loads, json.dumps, hashlib.sha256, type(registry).capabilities,
            type(registry).profiles.fget, type(registry).fingerprint.fget,
        )
        policy = (
            tuple((function, getattr(function, '__code__', None)) for function in functions),
            _TEST_ID, Mapping,
            CAPABILITY_EVIDENCE_SCHEMA_VERSION, frozenset(EVIDENCE_CLASSES),
            frozenset(MINIMUM_TRUSTED_EVIDENCE), frozenset(_INDEX_FIELDS),
            frozenset(_DECLARATION_FIELDS), type(registry),
        )
        key = (str(path.resolve()), registry.fingerprint,
               hashlib.sha256(text.encode("utf-8")).hexdigest(), policy)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key]
            # Validate the same captured bytes that established identity; a
            # second path read could certify different content under this key.
            value = evidence_storage.unpack_evidence_document(json.loads(text))
            fingerprint = validate_capability_evidence_index(value, registry=registry)
            self._entries[key] = fingerprint
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)
            return fingerprint


_DEFAULT_EVIDENCE_FINGERPRINT_CACHE = _VerifiedEvidenceFingerprintCache()


def _load_verified_default_capability_registry(registry_path):
    from .capabilities import CapabilityRegistry

    batch = _VALIDATION_REGISTRY.get()
    if batch is not None and "registry" in batch:
        return batch["registry"]
    registry = CapabilityRegistry.from_path(registry_path)
    fingerprint = _DEFAULT_EVIDENCE_FINGERPRINT_CACHE.fingerprint(
        registry, DEFAULT_CAPABILITY_EVIDENCE
    )
    registry.mark_evidence_verified(fingerprint)
    if batch is not None:
        batch["registry"] = registry
    return registry
