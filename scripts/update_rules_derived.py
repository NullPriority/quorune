from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quorune.rule_conformance import (
    build_rule_conformance,
    discover_unittest_ids,
    load_rule_conformance_reviews,
    rule_conformance_coverage,
    validate_rule_conformance,
)
from quorune.rules_corpus import (
    _json_hash,
    _mechanics_coverage,
    _mechanics_registry_document,
    _write_conformance_coverage,
    _write_coverage,
    _write_mechanics_coverage,
    rules_coverage,
)


class RulesDerivedError(RuntimeError):
    """The pinned rules reviews or mechanic contracts cannot be derived."""


OUTPUTS = (
    "rules/conformance-cases.json",
    "rules/manifest.json",
    "mechanics/registry.json",
    "coverage/mechanics-coverage.json",
    "coverage/mechanics-coverage.md",
    "coverage/rules-conformance.json",
    "coverage/rules-conformance.md",
    "coverage/rules-coverage.json",
    "coverage/rules-coverage.md",
)


def _load(relative: str) -> dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RulesDerivedError(f"{relative} must contain an object")
    return value


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _normalized_generated_bytes(path: Path) -> bytes:
    """Read generated text with platform newlines normalized to LF."""

    return path.read_text(encoding="utf-8").encode("utf-8")


def _pinned_manifest_metadata(manifest: Mapping[str, Any], documents: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Derive index metadata from the authoritative, source-bound inputs."""
    result = json.loads(json.dumps(manifest))
    for name, document in documents.items():
        if any(document.get(key) != manifest.get(key) for key in ('source_sha256', 'effective_date')):
            raise RulesDerivedError(f'{name} points to a different pinned rules snapshot')
    hashes = dict(result.get('derived_hashes') or {})
    hashes.update({name: _json_hash(document) for name, document in documents.items()})
    result['derived_hashes'] = hashes
    result['rule_count'] = len(documents['rule-index.json']['rules'])
    result['section_count'] = len(documents['rule-index.json']['sections'])
    result['glossary_count'] = len(documents['glossary-index.json']['entries'])
    result['mechanic_count'] = len(documents['mechanic-index.json']['mechanics'])
    return result


def expected_outputs() -> dict[str, bytes]:
    """Build review- and contract-derived artifacts without network access."""

    rule_index = _load("rules/rule-index.json")
    mechanic_index = _load("rules/mechanic-index.json")
    manifest = _load("rules/manifest.json")
    reviews, review_errors = load_rule_conformance_reviews(ROOT, rule_index)
    conformance = build_rule_conformance(
        rule_index,
        previous=None,
        reviews=reviews,
    )
    errors = [
        *review_errors,
        *validate_rule_conformance(
            conformance,
            rule_index,
            known_test_ids=discover_unittest_ids(ROOT),
        ),
    ]
    if errors:
        raise RulesDerivedError("; ".join(errors))

    mechanics_registry = _mechanics_registry_document(
        {
            "effective_date": rule_index.get("effective_date"),
            "rules": rule_index.get("rules", []),
            "mechanics": mechanic_index.get("mechanics", []),
        },
        card_data_snapshot=dict(manifest.get("card_data_snapshot") or {}),
        root=ROOT,
    )
    derived_manifest = _pinned_manifest_metadata(manifest, {
        'rule-index.json': rule_index,
        'mechanic-index.json': mechanic_index,
        'glossary-index.json': _load('rules/glossary-index.json'),
        'dependency-graph.json': _load('rules/dependency-graph.json'),
    })
    hashes = dict(derived_manifest.get("derived_hashes") or {})
    hashes["conformance-cases.json"] = _json_hash(conformance)
    hashes["mechanics/registry.json"] = _json_hash(mechanics_registry)
    derived_manifest["derived_hashes"] = hashes

    result = {
        "rules/conformance-cases.json": _json_bytes(conformance),
        "rules/manifest.json": _json_bytes(derived_manifest),
        "mechanics/registry.json": _json_bytes(mechanics_registry),
    }
    with TemporaryDirectory() as raw:
        temporary = Path(raw)
        (temporary / "rules").mkdir(parents=True)
        for name, value in (
            ("rule-index.json", rule_index),
            ("mechanic-index.json", mechanic_index),
            ("conformance-cases.json", conformance),
        ):
            (temporary / "rules" / name).write_bytes(_json_bytes(value))
        _write_mechanics_coverage(
            temporary,
            _mechanics_coverage(mechanics_registry),
        )
        _write_conformance_coverage(
            temporary,
            rule_conformance_coverage(conformance),
        )
        _write_coverage(temporary, rules_coverage(temporary))
        for relative in OUTPUTS[3:]:
            result[relative] = _normalized_generated_bytes(
                temporary / relative
            )
    return result


def _write(outputs: Mapping[str, bytes]) -> tuple[str, ...]:
    changed: list[str] = []
    for relative in OUTPUTS:
        path = ROOT / relative
        expected = outputs[relative]
        actual = path.read_bytes() if path.is_file() else None
        if actual == expected:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(expected)
        temporary.replace(path)
        changed.append(relative)
    return tuple(changed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh pinned rule-review and mechanic-contract derivatives "
            "without downloading or reparsing the rules corpus"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        outputs = expected_outputs()
    except (OSError, ValueError, RulesDerivedError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    stale = [
        relative
        for relative in OUTPUTS
        if not (ROOT / relative).is_file()
        or (ROOT / relative).read_bytes() != outputs[relative]
    ]
    changed = _write(outputs) if args.write else ()
    result = {
        "ok": args.write or not stale,
        "stale": stale,
        "changed": list(changed),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
