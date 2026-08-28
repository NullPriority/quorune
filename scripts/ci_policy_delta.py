from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence


ADDITIVE_SELECTION_AUTHORITY_PATHS = frozenset(
    {
        "platform/change-impact-policy.json",
        "platform/test-shards.json",
    }
)
_SELECTION_FIELDS = frozenset(
    {"checks", "test_modules", "test_suites", "browser_focuses", "symbols"}
)
_RULE_KEYS = frozenset(
    {
        "id",
        "patterns",
        "checks",
        "test_modules",
        "test_suites",
        "browser_focuses",
        "symbols",
        "collect_test_module",
        "browser_full",
        "windows_full",
    }
)


class CiPolicyDeltaError(ValueError):
    """A selection-authority change is malformed or is not monotonic."""


@dataclass(frozen=True, slots=True)
class CiPolicyDelta:
    additive: bool
    reasons: tuple[str, ...]


def _string_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise CiPolicyDeltaError(f"{field} must be a list of nonempty strings")
    if len(value) != len(set(value)):
        raise CiPolicyDeltaError(f"{field} must not contain duplicates")
    return tuple(value)


def _rules(value: object, *, field: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        raise CiPolicyDeltaError(f"{field} must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for index, rule in enumerate(value):
        if not isinstance(rule, Mapping):
            raise CiPolicyDeltaError(f"{field}[{index}] must be an object")
        unknown = set(rule) - _RULE_KEYS
        if unknown:
            raise CiPolicyDeltaError(
                f"{field}[{index}] has unknown fields: {sorted(unknown)}"
            )
        identifier = rule.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise CiPolicyDeltaError(f"{field}[{index}].id is invalid")
        if identifier in result:
            raise CiPolicyDeltaError(f"{field} contains duplicate id {identifier!r}")
        _string_list(rule.get("patterns"), field=f"{field}.{identifier}.patterns")
        for key in _SELECTION_FIELDS:
            if key in rule:
                _string_list(rule[key], field=f"{field}.{identifier}.{key}")
        for key in ("collect_test_module", "browser_full", "windows_full"):
            if key in rule and type(rule[key]) is not bool:
                raise CiPolicyDeltaError(f"{field}.{identifier}.{key} must be boolean")
        result[identifier] = rule
    return result


def _monotonic_rules(
    base_value: object,
    head_value: object,
    *,
    field: str,
) -> tuple[str, ...]:
    base = _rules(base_value, field=field)
    head = _rules(head_value, field=field)
    reasons: list[str] = []
    for identifier, base_rule in base.items():
        head_rule = head.get(identifier)
        if head_rule is None:
            reasons.append(f"removed-rule:{field}:{identifier}")
            continue
        for key in set(base_rule) | set(head_rule):
            if key in _SELECTION_FIELDS:
                base_items = set(
                    _string_list(
                        base_rule.get(key, []),
                        field=f"{field}.{identifier}.{key}",
                    )
                )
                head_items = set(
                    _string_list(
                        head_rule.get(key, []),
                        field=f"{field}.{identifier}.{key}",
                    )
                )
                if not base_items.issubset(head_items):
                    reasons.append(f"reduced-selection:{field}:{identifier}:{key}")
            elif base_rule.get(key) != head_rule.get(key):
                reasons.append(f"changed-rule-contract:{field}:{identifier}:{key}")
    for identifier, rule in head.items():
        if identifier in base:
            continue
        evidence = any(rule.get(field_name) for field_name in _SELECTION_FIELDS) or any(
            rule.get(field_name) is True
            for field_name in ("collect_test_module", "browser_full", "windows_full")
        )
        if not evidence:
            reasons.append(f"empty-added-rule:{field}:{identifier}")
    return tuple(sorted(set(reasons)))


def compare_change_impact_policy(
    base: Mapping[str, Any],
    head: Mapping[str, Any],
) -> CiPolicyDelta:
    required = {
        "schema_version",
        "default_checks",
        "browser_focuses",
        "risk_rules",
        "package_patterns",
        "path_rules",
        "symbol_rules",
        "fallback_test_suites",
        "forced_labels",
    }
    if set(base) != required or set(head) != required:
        return CiPolicyDelta(False, ("change-impact-policy-fields",))
    immutable = required - {"path_rules", "symbol_rules"}
    reasons = [
        f"changed-policy-contract:{field}"
        for field in sorted(immutable)
        if base[field] != head[field]
    ]
    try:
        reasons.extend(
            _monotonic_rules(
                base["path_rules"], head["path_rules"], field="path_rules"
            )
        )
        reasons.extend(
            _monotonic_rules(
                base["symbol_rules"], head["symbol_rules"], field="symbol_rules"
            )
        )
    except CiPolicyDeltaError as exc:
        reasons.append(f"malformed-policy:{exc}")
    canonical = tuple(sorted(set(reasons)))
    return CiPolicyDelta(not canonical, canonical)


def _module_owner(primary: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for shard, raw_modules in primary.items():
        modules = _string_list(raw_modules, field=f"primary_shards.{shard}")
        for module in modules:
            if module in result:
                raise CiPolicyDeltaError(
                    f"primary module {module!r} has duplicate ownership"
                )
            result[module] = str(shard)
    return result


def compare_test_shards(
    base: Mapping[str, Any],
    head: Mapping[str, Any],
    *,
    added_paths: Sequence[str] = (),
) -> CiPolicyDelta:
    required = {
        "schema_version",
        "execution_order",
        "primary_shards",
        "overlay_suites",
    }
    if set(base) != required or set(head) != required:
        return CiPolicyDelta(False, ("test-shards-fields",))
    reasons: list[str] = []
    if base["schema_version"] != head["schema_version"]:
        reasons.append("changed-test-shards-schema")
    if base["execution_order"] != head["execution_order"]:
        reasons.append("changed-test-shards-execution-order")
    base_primary = base.get("primary_shards")
    head_primary = head.get("primary_shards")
    base_overlays = base.get("overlay_suites")
    head_overlays = head.get("overlay_suites")
    if not all(
        isinstance(value, Mapping)
        for value in (base_primary, head_primary, base_overlays, head_overlays)
    ):
        return CiPolicyDelta(False, ("malformed-test-shards-maps",))
    assert isinstance(base_primary, Mapping)
    assert isinstance(head_primary, Mapping)
    assert isinstance(base_overlays, Mapping)
    assert isinstance(head_overlays, Mapping)
    if set(base_primary) != set(head_primary):
        reasons.append("changed-primary-shard-inventory")
    try:
        base_owner = _module_owner(base_primary)
        head_owner = _module_owner(head_primary)
        for module, shard in base_owner.items():
            if head_owner.get(module) != shard:
                reasons.append(f"moved-or-removed-primary-module:{module}")
        added_modules = set(head_owner) - set(base_owner)
        allowed_modules = {
            Path(path).stem
            for path in added_paths
            if path.startswith("tests/test_") and path.endswith(".py")
        }
        for module in sorted(added_modules - allowed_modules):
            reasons.append(f"assigned-nonadded-test-module:{module}")
        for suite, raw_base_modules in base_overlays.items():
            if suite not in head_overlays:
                reasons.append(f"removed-overlay-suite:{suite}")
                continue
            before = set(
                _string_list(raw_base_modules, field=f"overlay_suites.{suite}")
            )
            after = set(
                _string_list(
                    head_overlays[suite], field=f"overlay_suites.{suite}"
                )
            )
            if not before.issubset(after):
                reasons.append(f"reduced-overlay-suite:{suite}")
        for suite, raw_modules in head_overlays.items():
            modules = set(
                _string_list(raw_modules, field=f"overlay_suites.{suite}")
            )
            if not modules.issubset(head_owner):
                reasons.append(f"overlay-has-unowned-module:{suite}")
    except CiPolicyDeltaError as exc:
        reasons.append(f"malformed-test-shards:{exc}")
    canonical = tuple(sorted(set(reasons)))
    return CiPolicyDelta(not canonical, canonical)


def _json_at_ref(root: Path, ref: str, path: str) -> Mapping[str, Any]:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=root,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise CiPolicyDeltaError(
            result.stderr.strip() or f"unable to read {path} at {ref}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CiPolicyDeltaError(f"base {path} is malformed") from exc
    if not isinstance(value, Mapping):
        raise CiPolicyDeltaError(f"base {path} must contain an object")
    return value


def additive_selection_authority_paths(
    *,
    root: Path,
    base_ref: str,
    changed_paths: Sequence[str],
    added_paths: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    additive: list[str] = []
    reasons: list[str] = []
    for path in sorted(set(changed_paths) & ADDITIVE_SELECTION_AUTHORITY_PATHS):
        try:
            base = _json_at_ref(root, base_ref, path)
            head_value = json.loads((root / path).read_text(encoding="utf-8"))
            if not isinstance(head_value, Mapping):
                raise CiPolicyDeltaError(f"head {path} must contain an object")
            delta = (
                compare_change_impact_policy(base, head_value)
                if path == "platform/change-impact-policy.json"
                else compare_test_shards(
                    base,
                    head_value,
                    added_paths=added_paths,
                )
            )
        except (OSError, json.JSONDecodeError, CiPolicyDeltaError) as exc:
            reasons.append(f"nonadditive-selection-authority:{path}:{exc}")
            continue
        if delta.additive:
            additive.append(path)
        else:
            reasons.extend(
                f"nonadditive-selection-authority:{path}:{reason}"
                for reason in delta.reasons
            )
    return tuple(additive), tuple(sorted(set(reasons)))


__all__ = [
    "ADDITIVE_SELECTION_AUTHORITY_PATHS",
    "CiPolicyDelta",
    "CiPolicyDeltaError",
    "additive_selection_authority_paths",
    "compare_change_impact_policy",
    "compare_test_shards",
]
