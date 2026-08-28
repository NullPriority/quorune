from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "platform" / "change-impact-policy.json"


@dataclass(frozen=True)
class ImpactPlan:
    changed_files: tuple[str, ...]
    changed_symbols: tuple[str, ...]
    test_modules: tuple[str, ...]
    test_suites: tuple[str, ...]
    checks: tuple[str, ...]
    browser_full: bool
    browser_focuses: tuple[str, ...]
    browser_focus_patterns: tuple[str, ...]
    windows_full: bool
    policy_schema_version: int
    policy_fingerprint: str
    matched_rule_ids: tuple[str, ...]
    browser_full_reasons: tuple[str, ...]
    windows_full_reasons: tuple[str, ...]
    risk_class: str
    risk_reasons: tuple[str, ...]
    package_full: bool
    package_full_reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _normalized(paths: Iterable[str]) -> tuple[str, ...]:
    def normalize(path: str) -> str:
        value = str(path).strip().replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        return value.lstrip("/")

    return tuple(
        sorted(
            {
                normalize(path)
                for path in paths
                if str(path).strip()
            }
        )
    )


def changed_files(
    base: str,
    *,
    include_worktree: bool,
    diff_filter: str = "ACMRD",
    root: Path = ROOT,
) -> tuple[str, ...]:
    subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    separator = "" if include_worktree else "...HEAD"
    output = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            f"--diff-filter={diff_filter}",
            f"{base}{separator}",
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
    ).splitlines()
    if include_worktree:
        output.extend(
            subprocess.check_output(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=root,
                text=True,
                encoding="utf-8",
            ).splitlines()
        )
    return _normalized(output)


_HUNK = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<start>\d+)(?:,(?P<count>\d+))? @@(?P<context>.*)$"
)


def _python_symbols(source: str) -> tuple[tuple[str, int, int], ...]:
    """Return deterministic qualified function spans for one Python module."""

    tree = ast.parse(source)
    found: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, parents: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child,
                (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                name = ".".join((*parents, child.name))
                decorators = [
                    int(value.lineno)
                    for value in getattr(child, "decorator_list", ())
                ]
                start = min([int(child.lineno), *decorators])
                end = int(getattr(child, "end_lineno", child.lineno))
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append((name, start, end))
                visit(child, (*parents, child.name))
            else:
                visit(child, parents)

    visit(tree, ())
    return tuple(sorted(found))


def _symbols_for_ranges(
    source: str,
    ranges: Sequence[tuple[int, int, str]],
) -> tuple[str, ...]:
    spans = _python_symbols(source)
    selected: set[str] = set()
    for start, end, context in ranges:
        candidates = [
            name
            for name, span_start, span_end in spans
            if span_start <= end and span_end >= start
        ]
        if candidates:
            selected.update(candidates)
            continue
        context_match = re.search(
            r"(?:async\s+)?def\s+([A-Za-z_]\w*)",
            context,
        )
        if context_match:
            suffix = "." + context_match.group(1)
            matching = [name for name, _, _ in spans if name.endswith(suffix)]
            if len(matching) == 1:
                selected.add(matching[0])
    return tuple(sorted(selected))


def changed_python_symbols(
    base: str,
    *,
    include_worktree: bool,
    root: Path = ROOT,
) -> tuple[str, ...]:
    """Map changed Python hunks to their current qualified function owners."""

    subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    separator = "" if include_worktree else "...HEAD"
    comparison_base = (
        base
        if include_worktree
        else subprocess.check_output(
            ["git", "merge-base", base, "HEAD"],
            cwd=root,
            text=True,
            encoding="ascii",
        ).strip()
    )
    diff = subprocess.check_output(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-color",
            "--unified=0",
            "--diff-filter=ACMR",
            f"{base}{separator}",
            "--",
            "*.py",
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
    ).splitlines()
    ranges_by_path: dict[str, list[tuple[int, int, str]]] = {}
    old_ranges_by_path: dict[str, list[tuple[int, int, str]]] = {}
    current_path: str | None = None
    for line in diff:
        if line.startswith("+++ b/"):
            current_path = line[6:]
            continue
        match = _HUNK.match(line)
        if current_path is None or match is None:
            continue
        start = int(match.group("start"))
        count = int(match.group("count") or 1)
        end = start + max(count, 1) - 1
        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or 1)
        old_end = old_start + max(old_count, 1) - 1
        ranges_by_path.setdefault(current_path, []).append(
            (start, end, match.group("context").strip())
        )
        old_ranges_by_path.setdefault(current_path, []).append(
            (old_start, old_end, match.group("context").strip())
        )
    if include_worktree:
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "*.py"],
            cwd=root,
            text=True,
            encoding="utf-8",
        ).splitlines()
        for relative in untracked:
            path = root / relative
            if path.is_file():
                line_count = len(path.read_text(encoding="utf-8").splitlines())
                ranges_by_path.setdefault(relative.replace("\\", "/"), []).append(
                    (1, max(line_count, 1), "")
                )
    selected: set[str] = set()
    for relative, ranges in ranges_by_path.items():
        path = root / relative
        if not path.is_file():
            continue
        try:
            symbols = _symbols_for_ranges(path.read_text(encoding="utf-8"), ranges)
        except (SyntaxError, UnicodeDecodeError):
            continue
        selected.update(f"{relative}:{symbol}" for symbol in symbols)
    for relative, ranges in old_ranges_by_path.items():
        try:
            source = subprocess.check_output(
                ["git", "show", f"{comparison_base}:{relative}"],
                cwd=root,
                text=True,
                encoding="utf-8",
                stderr=subprocess.DEVNULL,
            )
            symbols = _symbols_for_ranges(source, ranges)
        except (subprocess.CalledProcessError, SyntaxError, UnicodeDecodeError):
            continue
        selected.update(f"{relative}:{symbol}" for symbol in symbols)
    return tuple(sorted(selected))


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a list of nonempty strings")
    return tuple(value)


def load_impact_policy(path: Path = POLICY_PATH) -> tuple[dict, str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Change-impact policy must be an object")
    if set(value) != {
        "schema_version",
        "default_checks",
        "browser_focuses",
        "risk_rules",
        "package_patterns",
        "path_rules",
        "symbol_rules",
        "fallback_test_suites",
        "forced_labels",
    }:
        raise ValueError("Change-impact policy has unknown or missing fields")
    if value["schema_version"] != 6:
        raise ValueError("Unsupported change-impact policy schema")
    _string_tuple(value["default_checks"], field="default_checks")
    browser_focuses = value["browser_focuses"]
    if (
        not isinstance(browser_focuses, dict)
        or not browser_focuses
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(pattern, str)
            or not pattern.startswith("@")
            for name, pattern in browser_focuses.items()
        )
    ):
        raise ValueError(
            "browser_focuses must map nonempty IDs to Playwright tags"
        )
    risk_rules = value["risk_rules"]
    if not isinstance(risk_rules, list) or not risk_rules:
        raise ValueError("risk_rules must be a nonempty list")
    risk_rule_ids: set[str] = set()
    for index, rule in enumerate(risk_rules):
        if not isinstance(rule, dict) or set(rule) != {
            "id",
            "patterns",
            "risk_class",
        }:
            raise ValueError(f"risk_rules[{index}] has invalid fields")
        rule_id = rule.get("id")
        if (
            not isinstance(rule_id, str)
            or not rule_id
            or rule_id in risk_rule_ids
        ):
            raise ValueError(f"risk_rules[{index}].id must be unique and nonempty")
        risk_rule_ids.add(rule_id)
        patterns = _string_tuple(
            rule.get("patterns"), field=f"risk_rules[{index}].patterns"
        )
        if not patterns:
            raise ValueError(f"risk_rules[{index}].patterns cannot be empty")
        if rule.get("risk_class") not in {
            "governance_only",
            "high_risk_source",
        }:
            raise ValueError(
                f"risk_rules[{index}].risk_class must be governance_only or high_risk_source"
            )
    package_patterns = _string_tuple(
        value["package_patterns"], field="package_patterns"
    )
    if not package_patterns:
        raise ValueError("package_patterns cannot be empty")
    rules = value["path_rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("path_rules must be a nonempty list")
    allowed_rule_fields = {
        "id",
        "patterns",
        "collect_test_module",
        "test_modules",
        "test_suites",
        "checks",
        "browser_focuses",
        "browser_full",
        "windows_full",
    }
    seen: set[str] = set(risk_rule_ids)
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not set(rule).issubset(allowed_rule_fields):
            raise ValueError(f"path_rules[{index}] has invalid fields")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id or rule_id in seen:
            raise ValueError(f"path_rules[{index}].id must be unique and nonempty")
        seen.add(rule_id)
        _string_tuple(rule.get("patterns"), field=f"path_rules[{index}].patterns")
        if not rule.get("patterns"):
            raise ValueError(f"path_rules[{index}].patterns cannot be empty")
        _string_tuple(rule.get("test_suites"), field=f"path_rules[{index}].test_suites")
        test_modules = _string_tuple(
            rule.get("test_modules"),
            field=f"path_rules[{index}].test_modules",
        )
        if any(
            re.fullmatch(r"test_[A-Za-z0-9_]+", item) is None
            for item in test_modules
        ):
            raise ValueError(
                f"path_rules[{index}].test_modules must name test modules"
            )
        _string_tuple(rule.get("checks"), field=f"path_rules[{index}].checks")
        selected_focuses = _string_tuple(
            rule.get("browser_focuses"),
            field=f"path_rules[{index}].browser_focuses",
        )
        unknown_focuses = sorted(set(selected_focuses).difference(browser_focuses))
        if unknown_focuses:
            raise ValueError(
                f"path_rules[{index}].browser_focuses are unknown: "
                + ", ".join(unknown_focuses)
            )
        for field in ("collect_test_module", "browser_full", "windows_full"):
            if field in rule and not isinstance(rule[field], bool):
                raise ValueError(f"path_rules[{index}].{field} must be boolean")
    symbol_rules = value["symbol_rules"]
    if not isinstance(symbol_rules, list):
        raise ValueError("symbol_rules must be a list")
    allowed_symbol_rule_fields = (
        allowed_rule_fields.difference({"collect_test_module"}) | {"symbols"}
    )
    for index, rule in enumerate(symbol_rules):
        if (
            not isinstance(rule, dict)
            or not set(rule).issubset(allowed_symbol_rule_fields)
        ):
            raise ValueError(f"symbol_rules[{index}] has invalid fields")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id or rule_id in seen:
            raise ValueError(f"symbol_rules[{index}].id must be unique and nonempty")
        seen.add(rule_id)
        patterns = _string_tuple(
            rule.get("patterns"), field=f"symbol_rules[{index}].patterns"
        )
        symbols = _string_tuple(
            rule.get("symbols"), field=f"symbol_rules[{index}].symbols"
        )
        if not patterns or not symbols:
            raise ValueError(
                f"symbol_rules[{index}] requires patterns and symbols"
            )
        _string_tuple(
            rule.get("test_suites"),
            field=f"symbol_rules[{index}].test_suites",
        )
        test_modules = _string_tuple(
            rule.get("test_modules"),
            field=f"symbol_rules[{index}].test_modules",
        )
        if any(
            re.fullmatch(r"test_[A-Za-z0-9_]+", item) is None
            for item in test_modules
        ):
            raise ValueError(
                f"symbol_rules[{index}].test_modules must name test modules"
            )
        _string_tuple(
            rule.get("checks"), field=f"symbol_rules[{index}].checks"
        )
        selected_focuses = _string_tuple(
            rule.get("browser_focuses"),
            field=f"symbol_rules[{index}].browser_focuses",
        )
        unknown_focuses = sorted(set(selected_focuses).difference(browser_focuses))
        if unknown_focuses:
            raise ValueError(
                f"symbol_rules[{index}].browser_focuses are unknown: "
                + ", ".join(unknown_focuses)
            )
        for field in ("browser_full", "windows_full"):
            if field in rule and not isinstance(rule[field], bool):
                raise ValueError(f"symbol_rules[{index}].{field} must be boolean")
    fallbacks = value["fallback_test_suites"]
    if not isinstance(fallbacks, list):
        raise ValueError("fallback_test_suites must be a list")
    for index, fallback in enumerate(fallbacks):
        if not isinstance(fallback, dict) or set(fallback) != {
            "id",
            "patterns",
            "test_suite",
        }:
            raise ValueError(f"fallback_test_suites[{index}] has invalid fields")
        if not isinstance(fallback["id"], str) or not fallback["id"]:
            raise ValueError(f"fallback_test_suites[{index}].id must be nonempty")
        _string_tuple(
            fallback["patterns"],
            field=f"fallback_test_suites[{index}].patterns",
        )
        if not isinstance(fallback["test_suite"], str) or not fallback["test_suite"]:
            raise ValueError(
                f"fallback_test_suites[{index}].test_suite must be nonempty"
            )
    labels = value["forced_labels"]
    if not isinstance(labels, dict) or not all(
        isinstance(key, str)
        and key
        and (
            target in {"browser_full", "windows_full", "high_risk_source"}
            or (
                isinstance(target, str)
                and target.startswith("browser_focus:")
                and target.removeprefix("browser_focus:") in browser_focuses
            )
        )
        for key, target in labels.items()
    ):
        raise ValueError("forced_labels must map labels to supported platform gates")
    fingerprint = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return value, fingerprint


def _matches_patterns(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def classify_changes(
    paths: Sequence[str],
    *,
    changed_symbols: Sequence[str] = (),
    labels: Sequence[str] = (),
    removed_paths: Sequence[str] = (),
    force_high_risk: bool = False,
    additive_selection_paths: Sequence[str] = (),
    policy_path: Path = POLICY_PATH,
) -> ImpactPlan:
    changed = _normalized(paths)
    removed = set(_normalized(removed_paths))
    additive_selection = set(_normalized(additive_selection_paths))
    allowed_additive_selection = {
        "platform/change-impact-policy.json",
        "platform/test-shards.json",
    }
    if not additive_selection.issubset(allowed_additive_selection):
        raise ValueError("additive_selection_paths contains unsupported authority")
    if not additive_selection.issubset(changed) or additive_selection & removed:
        raise ValueError("additive selection authority must be changed and present")
    normalized_symbols = tuple(sorted(set(changed_symbols)))
    normalized_labels = {label.casefold() for label in labels}
    policy, policy_fingerprint = load_impact_policy(policy_path)
    suites: set[str] = set()
    modules: set[str] = set()
    checks = set(_string_tuple(policy["default_checks"], field="default_checks"))
    matched_rule_ids: set[str] = set()
    browser_reasons: set[str] = set()
    browser_focuses: set[str] = set()
    windows_reasons: set[str] = set()
    risk_reasons: set[str] = set()
    path_risks: list[str] = []
    package_reasons: set[str] = set()

    if force_high_risk:
        risk_reasons.add("independent-sentinel")

    for path in changed:
        path_has_suite = False
        path_has_rule = False
        for rule in policy["path_rules"]:
            patterns = _string_tuple(rule["patterns"], field=f"{rule['id']}.patterns")
            if not _matches_patterns(path, patterns):
                continue
            path_has_rule = True
            rule_id = str(rule["id"])
            matched_rule_ids.add(rule_id)
            selected_suites = _string_tuple(
                rule.get("test_suites"), field=f"{rule_id}.test_suites"
            )
            if selected_suites:
                path_has_suite = True
                suites.update(selected_suites)
            modules.update(
                _string_tuple(
                    rule.get("test_modules"),
                    field=f"{rule_id}.test_modules",
                )
            )
            checks.update(_string_tuple(rule.get("checks"), field=f"{rule_id}.checks"))
            browser_focuses.update(
                _string_tuple(
                    rule.get("browser_focuses"),
                    field=f"{rule_id}.browser_focuses",
                )
            )
            if rule.get("collect_test_module"):
                modules.add(Path(path).stem)
            if rule.get("browser_full"):
                browser_reasons.add(f"path:{path}:{rule_id}")
            if rule.get("windows_full"):
                windows_reasons.add(f"path:{path}:{rule_id}")
        if not path_has_suite:
            for fallback in policy["fallback_test_suites"]:
                patterns = _string_tuple(
                    fallback["patterns"], field=f"{fallback['id']}.patterns"
                )
                if _matches_patterns(path, patterns):
                    suites.add(str(fallback["test_suite"]))
                    matched_rule_ids.add(str(fallback["id"]))
                    path_has_rule = True
                    break

        matched_risks = [
            rule
            for rule in policy["risk_rules"]
            if _matches_patterns(path, rule["patterns"])
        ]
        if path in additive_selection:
            path_risks.append("ordinary_source")
            risk_reasons.add(f"additive-selection-authority:{path}")
        elif path in removed:
            path_risks.append("high_risk_source")
            risk_reasons.add(f"removed:{path}")
        elif any(
            rule["risk_class"] == "high_risk_source"
            for rule in matched_risks
        ):
            path_risks.append("high_risk_source")
            risk_reasons.update(
                f"path:{path}:{rule['id']}"
                for rule in matched_risks
                if rule["risk_class"] == "high_risk_source"
            )
        elif not path_has_rule:
            path_risks.append("high_risk_source")
            risk_reasons.add(f"unclassified:{path}")
        elif matched_risks and all(
            rule["risk_class"] == "governance_only"
            for rule in matched_risks
        ):
            path_risks.append("governance_only")
            risk_reasons.update(
                f"path:{path}:{rule['id']}" for rule in matched_risks
            )
        else:
            path_risks.append("ordinary_source")
        if _matches_patterns(path, policy["package_patterns"]):
            package_reasons.add(f"path:{path}")

    for entry in normalized_symbols:
        path, separator, symbol = entry.rpartition(":")
        if not separator or not path or not symbol:
            raise ValueError("changed_symbols entries must be path:symbol")
        for rule in policy["symbol_rules"]:
            if not _matches_patterns(path, rule["patterns"]):
                continue
            if symbol not in _string_tuple(
                rule["symbols"], field=f"{rule['id']}.symbols"
            ):
                continue
            rule_id = str(rule["id"])
            matched_rule_ids.add(rule_id)
            suites.update(
                _string_tuple(
                    rule.get("test_suites"), field=f"{rule_id}.test_suites"
                )
            )
            modules.update(
                _string_tuple(
                    rule.get("test_modules"),
                    field=f"{rule_id}.test_modules",
                )
            )
            checks.update(
                _string_tuple(rule.get("checks"), field=f"{rule_id}.checks")
            )
            browser_focuses.update(
                _string_tuple(
                    rule.get("browser_focuses"),
                    field=f"{rule_id}.browser_focuses",
                )
            )
            if rule.get("browser_full"):
                browser_reasons.add(f"symbol:{path}:{symbol}:{rule_id}")
            if rule.get("windows_full"):
                windows_reasons.add(f"symbol:{path}:{symbol}:{rule_id}")

    for label, target in policy["forced_labels"].items():
        if label.casefold() not in normalized_labels:
            continue
        if target == "browser_full":
            browser_reasons.add(f"label:{label}")
        elif target == "windows_full":
            windows_reasons.add(f"label:{label}")
        elif target == "high_risk_source":
            force_high_risk = True
            risk_reasons.add(f"label:{label}")
        elif target.startswith("browser_focus:"):
            browser_focuses.add(target.removeprefix("browser_focus:"))
    risk_class = (
        "high_risk_source"
        if force_high_risk or "high_risk_source" in path_risks or not changed
        else (
            "ordinary_source"
            if "ordinary_source" in path_risks
            else "governance_only"
        )
    )
    if risk_class == "high_risk_source":
        browser_reasons.add("risk:high_risk_source")
        windows_reasons.add("risk:high_risk_source")
        package_reasons.add("risk:high_risk_source")
    ordered_focuses = tuple(sorted(browser_focuses))
    return ImpactPlan(
        changed_files=changed,
        changed_symbols=normalized_symbols,
        test_modules=tuple(sorted(modules)),
        test_suites=tuple(sorted(suites)),
        checks=tuple(sorted(checks)),
        browser_full=bool(browser_reasons),
        browser_focuses=ordered_focuses,
        browser_focus_patterns=tuple(
            str(policy["browser_focuses"][focus])
            for focus in ordered_focuses
        ),
        windows_full=bool(windows_reasons),
        policy_schema_version=int(policy["schema_version"]),
        policy_fingerprint=policy_fingerprint,
        matched_rule_ids=tuple(sorted(matched_rule_ids)),
        browser_full_reasons=tuple(sorted(browser_reasons)),
        windows_full_reasons=tuple(sorted(windows_reasons)),
        risk_class=risk_class,
        risk_reasons=tuple(sorted(risk_reasons)),
        package_full=bool(package_reasons),
        package_full_reasons=tuple(sorted(package_reasons)),
    )


def github_event_labels(path: str | None = None) -> tuple[str, ...]:
    event_path = path or os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return ()
    value = json.loads(Path(event_path).read_text(encoding="utf-8"))
    labels = value.get("pull_request", {}).get("labels", [])
    return tuple(
        str(label.get("name"))
        for label in labels
        if isinstance(label, dict) and label.get("name")
    )


def github_base(path: str | None = None) -> str:
    event_path = path or os.environ.get("GITHUB_EVENT_PATH")
    if event_path:
        value = json.loads(Path(event_path).read_text(encoding="utf-8"))
        base = value.get("pull_request", {}).get("base", {}).get("sha")
        if base:
            return str(base)
    return "HEAD^"
