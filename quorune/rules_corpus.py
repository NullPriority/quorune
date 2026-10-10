from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .mechanic_contracts import (
    MechanicContractError,
    apply_contracts_to_registry,
    load_mechanic_contracts,
)
from .rule_conformance import (
    REVIEW_FIELDS,
    build_rule_conformance,
    discover_unittest_ids,
    load_rule_conformance_reviews,
    rule_conformance_coverage,
    validate_rule_conformance,
)
from .rules_scheduler import (
    load_rules_dependency_queue,
    rules_dependency_queue_errors,
    rules_next_work,
)

RULES_CORPUS_SCHEMA_VERSION = 1
RULES_PARSER_VERSION = "cr-index-v2"
OFFICIAL_RULES_PAGE = "https://magic.wizards.com/en/rules"
TRUSTED_RULES_HOSTS = {"magic.wizards.com", "media.wizards.com"}
COVERAGE_STATUSES = {
    "unclassified",
    "definition_only",
    "planned",
    "partial",
    "implemented",
    "tested",
    "trusted",
    "not_applicable_to_profile",
    "non_rules_governed",
}
CORPUS_OPERATIONS = {
    "sync",
    "inventory",
    "diff",
    "coverage",
    "conformance",
    "queue",
    "next",
    "verify",
    "report",
}

_RULE_LINE = re.compile(
    r"^(?P<id>\d{3}(?:\.\d+[a-z]*)?)\.?\s+(?P<body>.+?)\s*$"
)
_MAJOR_SECTION = re.compile(r"^(?P<id>[1-9])\.\s+(?P<title>.+?)\s*$")
_EFFECTIVE_DATE = re.compile(
    r"rules are effective as of (?P<date>[^.]+)\.",
    re.IGNORECASE,
)
_TXT_HREF = re.compile(
    r"""href\s*=\s*["'](?P<href>[^"']*MagicCompRules[^"']*\.txt(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)


class RulesCorpusError(ValueError):
    pass


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        trusted_url = _trusted_https_url(newurl)
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            trusted_url,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_hash(value: Any) -> str:
    return _sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _trusted_https_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname not in TRUSTED_RULES_HOSTS
    ):
        raise RulesCorpusError(
            "Rules downloads must use HTTPS on an official Wizards host"
        )
    safe_path = urllib.parse.quote(
        urllib.parse.unquote(parsed.path),
        safe="/~:@!$&'()*+,;=-._",
    )
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            safe_path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def _download(url: str, *, timeout: float = 30.0) -> tuple[bytes, str]:
    url = _trusted_https_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/plain,text/html;q=0.9,*/*;q=0.1",
            "User-Agent": (
                "quorune-rules-corpus/"
                f"{RULES_PARSER_VERSION}"
            ),
        },
    )
    try:
        opener = urllib.request.build_opener(_TrustedRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            final_url = _trusted_https_url(str(response.geturl()))
            payload = response.read(12 * 1024 * 1024 + 1)
    except (
        OSError,
        urllib.error.HTTPError,
        urllib.error.URLError,
    ) as exc:
        raise RulesCorpusError(
            f"Unable to download official rules source {url}: {exc}"
        ) from exc
    if len(payload) > 12 * 1024 * 1024:
        raise RulesCorpusError("Comprehensive Rules download is unexpectedly large")
    return payload, final_url


def locate_official_rules_txt(
    *,
    page_url: str = OFFICIAL_RULES_PAGE,
    timeout: float = 30.0,
) -> str:
    payload, final_page = _download(page_url, timeout=timeout)
    document = payload.decode("utf-8", errors="replace")
    candidates = [
        urllib.parse.urljoin(final_page, html.unescape(match.group("href")))
        for match in _TXT_HREF.finditer(document)
    ]
    if not candidates:
        raise RulesCorpusError(
            "The official Magic Rules page did not publish a TXT rules link"
        )
    for candidate in reversed(candidates):
        try:
            return _trusted_https_url(candidate)
        except RulesCorpusError:
            continue
    raise RulesCorpusError(
        "The official Magic Rules page linked no trusted TXT rules source"
    )


def _effective_date(text: str) -> str:
    match = _EFFECTIVE_DATE.search(text[:5000])
    if not match:
        raise RulesCorpusError(
            "Could not identify the Comprehensive Rules effective date"
        )
    raw = " ".join(match.group("date").split())
    try:
        return datetime.strptime(raw, "%B %d, %Y").date().isoformat()
    except ValueError as exc:
        raise RulesCorpusError(
            f"Unrecognized Comprehensive Rules effective date {raw!r}"
        ) from exc


def _parent_rule_id(rule_id: str) -> str | None:
    letter = re.match(r"^(?P<parent>\d{3}\.\d+)[a-z]+$", rule_id)
    if letter:
        return letter.group("parent")
    if "." in rule_id:
        return rule_id.rsplit(".", 1)[0]
    return None


def _category_for_rule(rule_id: str) -> str:
    number = int(rule_id.split(".", 1)[0])
    if 100 <= number <= 123:
        return "game_concepts"
    if 200 <= number <= 213:
        return "card_characteristics"
    if 300 <= number <= 315:
        return "card_types"
    if 400 <= number <= 408:
        return "zones"
    if 500 <= number <= 514:
        return "turn_structure"
    if 600 <= number <= 616:
        return "spells_abilities_effects"
    if 700 <= number <= 733:
        return "additional_rules"
    if 800 <= number <= 811:
        return "multiplayer"
    if number == 903:
        return "commander"
    if 900 <= number <= 905:
        return "casual_variants"
    return "uncategorized"


def _heading_candidate(rule_id: str, body: str) -> str | None:
    if rule_id.isdigit():
        return body if len(body) <= 100 else None
    if not (
        rule_id.startswith("701.")
        or rule_id.startswith("702.")
    ):
        return None
    if (
        len(body) <= 100
        and "\n" not in body
        and (not body.endswith((".", "!", "?")) or bool(re.fullmatch(r'70[12]\.\d+',rule_id) and re.fullmatch(r"[A-Za-z][A-Za-z '’-]*!",body)))
    ):
        return body
    return None


def parse_comprehensive_rules(
    text: str,
    *,
    source_sha256: str,
) -> dict[str, Any]:
    """Parse a CR snapshot without returning or persisting rules prose."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    effective_date = _effective_date(normalized)
    glossary_lines = [
        index
        for index, line in enumerate(lines)
        if line.strip() == "Glossary"
    ]
    glossary_start = glossary_lines[-1] if glossary_lines else len(lines)
    game_concepts_lines = [
        index
        for index, line in enumerate(lines[:glossary_start])
        if line.strip() == "1. Game Concepts"
    ]
    rules_start = (
        game_concepts_lines[-1] if game_concepts_lines else 0
    )

    major_section: dict[str, Any] | None = None
    section_heading: dict[str, Any] | None = None
    sections: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    pending_text: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal pending, pending_text
        if pending is None:
            return
        body = "\n".join(
            value.strip() for value in pending_text if value.strip()
        ).strip()
        rule_id = str(pending["rule_id"])
        parent = _parent_rule_id(rule_id)
        heading = _heading_candidate(rule_id, body)
        records.append(
            {
                "rule_id": rule_id,
                "parent_rule_id": parent,
                "section": dict(pending["section"]),
                "effective_date": effective_date,
                "source_sha256": source_sha256,
                "source_span": {
                    "start_line": int(pending["start_line"]),
                    "end_line": max(
                        int(pending["start_line"]),
                        end_line,
                    ),
                },
                "text_sha256": _sha256(body.encode("utf-8")),
                "text_length": len(body),
                "heading": heading,
                "short_summary": None,
                "summary_reviewed": False,
                "behavioral_classification": "unclassified",
                "implementation_component": None,
                "test_ids": [],
                "dependency_ids": [parent] if parent else [],
                "coverage_status": "unclassified",
                "applicability_profiles": ["all"],
                "notes": [],
            }
        )
        pending = None
        pending_text = []

    for index in range(rules_start, glossary_start):
        raw = lines[index]
        line = raw.strip()
        major = _MAJOR_SECTION.match(line)
        rule = _RULE_LINE.match(line)
        if major and not rule:
            flush(index)
            major_section = {
                "id": major.group("id"),
                "title": major.group("title"),
            }
            sections.append(
                {
                    "section_id": major_section["id"],
                    "title": major_section["title"],
                    "level": "major",
                    "source_line": index + 1,
                }
            )
            section_heading = None
            continue
        if rule:
            flush(index)
            rule_id = rule.group("id")
            body = rule.group("body").strip()
            if rule_id.isdigit():
                section_heading = {
                    "id": rule_id,
                    "title": body,
                }
                sections.append(
                    {
                        "section_id": rule_id,
                        "title": body,
                        "level": "rules_section",
                        "major_section_id": (
                            major_section["id"] if major_section else None
                        ),
                        "source_line": index + 1,
                    }
                )
            effective_section = section_heading or major_section or {
                "id": "unknown",
                "title": "Unknown",
            }
            pending = {
                "rule_id": rule_id,
                "start_line": index + 1,
                "section": effective_section,
            }
            pending_text = [body]
            continue
        if pending is not None and line:
            pending_text.append(line)
    flush(glossary_start)

    glossary = _parse_glossary(
        lines,
        glossary_start=glossary_start,
        effective_date=effective_date,
        source_sha256=source_sha256,
    )
    mechanics = _mechanic_index(records)
    return {
        "effective_date": effective_date,
        "rules": records,
        "sections": sections,
        "glossary": glossary,
        "mechanics": mechanics,
    }


def _parse_glossary(
    lines: Sequence[str],
    *,
    glossary_start: int,
    effective_date: str,
    source_sha256: str,
) -> list[dict[str, Any]]:
    if glossary_start >= len(lines):
        return []
    credits = next(
        (
            index
            for index in range(glossary_start + 1, len(lines))
            if lines[index].strip() == "Credits"
        ),
        len(lines),
    )
    blocks: list[tuple[int, int, list[str]]] = []
    start: int | None = None
    values: list[str] = []
    for index in range(glossary_start + 1, credits):
        value = lines[index].strip()
        if value:
            if start is None:
                start = index
            values.append(value)
        elif values:
            blocks.append((int(start), index - 1, values))
            start = None
            values = []
    if values:
        blocks.append((int(start), credits - 1, values))

    entries: list[dict[str, Any]] = []
    for start_index, end_index, block in blocks:
        if len(block) < 2:
            continue
        term = block[0]
        definition = "\n".join(block[1:])
        if len(term) > 120 or term.endswith((".", "!", "?")):
            continue
        entries.append(
            {
                "term": term,
                "term_id": re.sub(
                    r"[^a-z0-9]+",
                    "-",
                    term.casefold(),
                ).strip("-"),
                "effective_date": effective_date,
                "source_sha256": source_sha256,
                "source_span": {
                    "start_line": start_index + 1,
                    "end_line": end_index + 1,
                },
                "definition_sha256": _sha256(
                    definition.encode("utf-8")
                ),
                "definition_length": len(definition),
                "rule_references": sorted(
                    set(
                        re.findall(
                            r"\brule\s+(\d{3}(?:\.\d+[a-z]?)?)",
                            definition,
                            flags=re.IGNORECASE,
                        )
                    )
                ),
                "short_summary": None,
                "summary_reviewed": False,
                "coverage_status": "unclassified",
            }
        )
    return entries


def _mechanic_index(rules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    mechanics: list[dict[str, Any]] = []
    for rule in rules:
        rule_id = str(rule["rule_id"])
        heading = rule.get("heading")
        if not heading:
            continue
        if rule_id.isdigit():
            kind = "rules_section"
        elif rule_id.startswith("701."):
            kind = "keyword_action"
        elif rule_id.startswith("702."):
            kind = "keyword_ability"
        else:
            continue
        mechanic_id = re.sub(
            r"[^a-z0-9]+",
            "-",
            str(heading).casefold(),
        ).strip("-")
        if kind == "rules_section":
            mechanic_id = f"cr-{rule_id}-{mechanic_id}"
        mechanics.append(
            {
                "mechanic_id": mechanic_id,
                "official_name": heading,
                "kind": kind,
                "category": _category_for_rule(rule_id),
                "rule_references": [rule_id],
                "dependencies": list(rule.get("dependency_ids") or []),
                "coverage_status": "unclassified",
                "contract_path": None,
                "implementation_component": None,
                "test_ids": [],
                "trust_level": "untrusted",
            }
        )
    mechanics.sort(key=lambda item: (item["kind"], item["mechanic_id"]))
    return mechanics


def _derived_documents(
    parsed: Mapping[str, Any],
    *,
    source_url: str,
    source_sha256: str,
    source_size: int,
    cached_filename: str,
    card_data_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rules = list(parsed["rules"])
    glossary = list(parsed["glossary"])
    mechanics = list(parsed["mechanics"])
    effective_date = str(parsed["effective_date"])
    manifest = {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "parser_version": RULES_PARSER_VERSION,
        "source_page_url": OFFICIAL_RULES_PAGE,
        "source_url": source_url,
        "effective_date": effective_date,
        "source_sha256": source_sha256,
        "source_size_bytes": source_size,
        "cached_filename": cached_filename,
        "generated_at": _utc_now(),
        "rule_count": len(rules),
        "section_count": len(parsed["sections"]),
        "glossary_count": len(glossary),
        "mechanic_count": len(mechanics),
        "card_data_snapshot": dict(
            card_data_snapshot or {"available": False}
        ),
        "derived_hashes": {},
    }
    rule_index = {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "parser_version": RULES_PARSER_VERSION,
        "effective_date": effective_date,
        "source_sha256": source_sha256,
        "sections": parsed["sections"],
        "rules": rules,
    }
    glossary_index = {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "parser_version": RULES_PARSER_VERSION,
        "effective_date": effective_date,
        "source_sha256": source_sha256,
        "entries": glossary,
    }
    mechanic_index = {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "parser_version": RULES_PARSER_VERSION,
        "effective_date": effective_date,
        "source_sha256": source_sha256,
        "mechanics": mechanics,
    }
    graph = {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "effective_date": effective_date,
        "source_sha256": source_sha256,
        "nodes": [
            {
                "id": rule["rule_id"],
                "kind": "rule",
                "coverage_status": rule["coverage_status"],
            }
            for rule in rules
        ],
        "edges": [
            {
                "from": rule["rule_id"],
                "to": dependency,
                "kind": "depends_on",
            }
            for rule in rules
            for dependency in rule.get("dependency_ids", [])
        ],
    }
    documents = {
        "rule-index.json": rule_index,
        "glossary-index.json": glossary_index,
        "mechanic-index.json": mechanic_index,
        "dependency-graph.json": graph,
    }
    manifest["derived_hashes"] = {
        name: _json_hash(value) for name, value in documents.items()
    }
    return {"manifest.json": manifest, **documents}


def _mechanics_registry_document(
    parsed: Mapping[str, Any],
    *,
    card_data_snapshot: Mapping[str, Any],
    root: str | Path | None = None,
) -> dict[str, Any]:
    mechanics = list(parsed["mechanics"])
    taxonomy = [
        {
            "taxonomy_id": mechanic["mechanic_id"],
            "official_name": mechanic["official_name"],
            "category": mechanic["category"],
            "rule_references": mechanic["rule_references"],
        }
        for mechanic in mechanics
        if mechanic["kind"] == "rules_section"
    ]
    registry = {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "registry_version": "mechanics-registry-v1",
        "effective_date": parsed["effective_date"],
        "source_sha256": (
            parsed["rules"][0]["source_sha256"]
            if parsed["rules"]
            else None
        ),
        "sources": {
            "comprehensive_rules": "indexed",
            "oracle_keywords": "planned",
            "oracle_templates": "planned",
            "official_set_mechanics": "planned",
            "card_data_snapshot": dict(card_data_snapshot),
        },
        "taxonomy": taxonomy,
        "mechanics": mechanics,
        "generation_status": "cr_index_only",
        "oracle_enrichment_complete": False,
        "trusted_mechanic_count": sum(
            mechanic.get("trust_level") == "trusted"
            for mechanic in mechanics
        ),
    }
    if root is None:
        return registry
    contracts = load_mechanic_contracts(
        root,
        expected_effective_date=str(parsed["effective_date"]),
        expected_source_sha256=(
            str(parsed["rules"][0]["source_sha256"])
            if parsed["rules"]
            else None
        ),
        known_rule_ids={
            str(rule["rule_id"]) for rule in parsed["rules"]
        },
    )
    return apply_contracts_to_registry(registry, contracts)


def _mechanics_coverage(
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    mechanics = list(registry.get("mechanics", []))
    statuses = Counter(
        str(mechanic.get("coverage_status") or "unclassified")
        for mechanic in mechanics
    )
    trusted = sum(
        mechanic.get("trust_level") == "trusted"
        for mechanic in mechanics
    )
    return {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "effective_date": registry.get("effective_date"),
        "source_sha256": registry.get("source_sha256"),
        "total_mechanics": len(mechanics),
        "status_counts": dict(sorted(statuses.items())),
        "trusted_mechanics": trusted,
        "trusted_fraction": (
            round(trusted / len(mechanics), 6) if mechanics else 0.0
        ),
        "oracle_enrichment_complete": bool(
            registry.get("oracle_enrichment_complete")
        ),
        "current_snapshot_complete": bool(mechanics)
        and trusted == len(mechanics)
        and bool(registry.get("oracle_enrichment_complete")),
    }


def _write_mechanics_coverage(
    root: str | Path,
    coverage: Mapping[str, Any],
) -> None:
    directory = Path(root) / "coverage"
    _atomic_json(directory / "mechanics-coverage.json", coverage)
    _atomic_text(
        directory / "mechanics-coverage.md",
        "\n".join(
            [
                f'---\ntitle: "Mechanics coverage"\nstatus: "generated"\nauthoritative_source: "coverage/mechanics-coverage.json"\nverified: "{coverage.get("effective_date")}"\naudience: "rules and compiler contributors"\nmaintenance: "generated"\n---\n\n# Mechanics coverage',
                "",
                f"- Effective date: `{coverage.get('effective_date')}`",
                f"- Discovered mechanics: {coverage.get('total_mechanics', 0)}",
                f"- Trusted mechanics: {coverage.get('trusted_mechanics', 0)}",
                (
                    "- Oracle enrichment complete: "
                    + str(
                        bool(
                            coverage.get(
                                "oracle_enrichment_complete"
                            )
                        )
                    ).lower()
                ),
                (
                    "- Current snapshot complete: "
                    + str(
                        bool(
                            coverage.get(
                                "current_snapshot_complete"
                            )
                        )
                    ).lower()
                ),
                "",
                "A mechanic becomes trusted only after a versioned contract, "
                "implementation mapping, and conformance tests are recorded.",
                "",
            ]
        ),
    )


def sync_rules_corpus(
    root: str | Path,
    *,
    cache_dir: str | Path | None = None,
    source_file: str | Path | None = None,
    source_url: str | None = None,
    card_db_path: str | Path | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    root = Path(root)
    rules_dir = root / "rules"
    cache = (
        Path(cache_dir)
        if cache_dir is not None
        else root / "local" / "rules"
    )
    previous = _load_json_if_exists(rules_dir / "rule-index.json")
    previous_conformance = _load_json_if_exists(
        rules_dir / "conformance-cases.json"
    )
    previous_manifest = _load_json_if_exists(
        rules_dir / "manifest.json"
    )

    if source_file is not None:
        payload = Path(source_file).read_bytes()
        resolved_url = source_url or "local-source"
    else:
        resolved_url = source_url or locate_official_rules_txt(
            timeout=timeout
        )
        payload, resolved_url = _download(
            resolved_url,
            timeout=timeout,
        )
    source_sha256 = _sha256(payload)
    text = payload.decode("utf-8-sig")
    parsed = parse_comprehensive_rules(
        text,
        source_sha256=source_sha256,
    )
    cached_filename = (
        f"MagicCompRules-{parsed['effective_date']}-{source_sha256[:12]}.txt"
    )
    cache.mkdir(parents=True, exist_ok=True)
    cached_path = cache / cached_filename
    if not cached_path.exists():
        cached_path.write_bytes(payload)

    card_data_snapshot = _card_data_snapshot(card_db_path)
    documents = _derived_documents(
        parsed,
        source_url=resolved_url,
        source_sha256=source_sha256,
        source_size=len(payload),
        cached_filename=cached_filename,
        card_data_snapshot=card_data_snapshot,
    )
    mechanics_registry = _mechanics_registry_document(
        parsed,
        card_data_snapshot=card_data_snapshot,
        root=root,
    )
    conformance_reviews, review_errors = (
        load_rule_conformance_reviews(
            root,
            documents["rule-index.json"],
        )
    )
    # Once the review directory exists, its source-pinned overlays are the
    # authoritative review source.  Falling back to the generated artifact
    # would make a deleted or stale overlay survive indefinitely.
    previous_for_build = (
        None
        if (root / "rules" / "conformance-reviews").is_dir()
        else previous_conformance
    )
    conformance = build_rule_conformance(
        documents["rule-index.json"],
        previous=previous_for_build,
        reviews=conformance_reviews,
    )
    conformance_errors = validate_rule_conformance(
        conformance,
        documents["rule-index.json"],
        known_test_ids=discover_unittest_ids(root),
    )
    documents["conformance-cases.json"] = conformance
    documents["manifest.json"]["derived_hashes"][
        "conformance-cases.json"
    ] = _json_hash(conformance)
    documents["manifest.json"]["derived_hashes"][
        "mechanics/registry.json"
    ] = _json_hash(mechanics_registry)
    if (
        previous_manifest is not None
        and previous_manifest.get("source_sha256") == source_sha256
        and previous_manifest.get("derived_hashes")
        == documents["manifest.json"].get("derived_hashes")
        and previous_manifest.get("card_data_snapshot")
        == documents["manifest.json"].get("card_data_snapshot")
    ):
        documents["manifest.json"]["generated_at"] = (
            previous_manifest.get("generated_at")
            or documents["manifest.json"]["generated_at"]
        )
    for name, value in documents.items():
        _atomic_json(rules_dir / name, value)
    _atomic_json(root / "mechanics" / "registry.json", mechanics_registry)
    _write_mechanics_coverage(
        root,
        _mechanics_coverage(mechanics_registry),
    )
    conformance_coverage = rule_conformance_coverage(conformance)
    _write_conformance_coverage(root, conformance_coverage)

    coverage = rules_coverage(root)
    _write_coverage(root, coverage)
    delta = None
    if previous is not None:
        delta = compare_rule_indexes(
            documents["rule-index.json"],
            previous,
        )
        delta["generated_at"] = documents["manifest.json"][
            "generated_at"
        ]
        _write_delta(root, delta)
    return {
        "ok": not review_errors and not conformance_errors,
        "review_errors": review_errors,
        "conformance_errors": conformance_errors,
        "rules_dir": str(rules_dir),
        "cache_file": str(cached_path),
        "manifest": documents["manifest.json"],
        "coverage": coverage,
        "delta": delta,
    }


def _card_data_snapshot(
    card_db_path: str | Path | None,
) -> dict[str, Any]:
    if card_db_path is None:
        return {"available": False}
    from .carddb import CardDatabase, file_sha256

    database_path = Path(card_db_path)
    with CardDatabase(database_path) as database:
        metadata = database.metadata()

    def source_hash(kind: str) -> str | None:
        recorded = metadata.get(f"{kind}_source_sha256")
        if recorded:
            return recorded
        source = metadata.get(f"{kind}_source")
        if not source:
            return None
        path = Path(source)
        if not path.is_file():
            return None
        return file_sha256(path)

    return {
        "available": True,
        "database_schema_version": metadata.get("schema_version"),
        "oracle_bulk": {
            "updated_at": metadata.get("scryfall_oracle_updated_at"),
            "sha256": source_hash("oracle"),
            "source_filename": (
                Path(metadata["oracle_source"]).name
                if metadata.get("oracle_source")
                else None
            ),
            "download_uri": metadata.get(
                "scryfall_oracle_download_uri"
            ),
            "oracle_id_count": (
                int(metadata["card_count"])
                if metadata.get("card_count")
                else None
            ),
        },
        "rulings_bulk": {
            "updated_at": metadata.get("scryfall_rulings_updated_at"),
            "sha256": source_hash("rulings"),
            "source_filename": (
                Path(metadata["rulings_source"]).name
                if metadata.get("rulings_source")
                else None
            ),
            "download_uri": metadata.get(
                "scryfall_rulings_download_uri"
            ),
            "ruling_count": (
                int(metadata["ruling_count"])
                if metadata.get("ruling_count")
                else None
            ),
        },
    }


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_required(root: str | Path, relative: str) -> dict[str, Any]:
    path = Path(root) / relative
    if not path.exists():
        raise RulesCorpusError(
            f"Rules corpus file is missing: {path}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def rules_inventory(root: str | Path) -> dict[str, Any]:
    manifest = _load_required(root, "rules/manifest.json")
    rule_index = _load_required(root, "rules/rule-index.json")
    glossary = _load_required(root, "rules/glossary-index.json")
    mechanics = _load_required(root, "rules/mechanic-index.json")
    conformance = _load_required(
        root, "rules/conformance-cases.json"
    )
    conformance_coverage = rule_conformance_coverage(conformance)
    statuses = Counter(
        str(rule.get("coverage_status") or "unclassified")
        for rule in rule_index.get("rules", [])
    )
    return {
        "ok": True,
        "effective_date": manifest.get("effective_date"),
        "source_sha256": manifest.get("source_sha256"),
        "parser_version": manifest.get("parser_version"),
        "rules": len(rule_index.get("rules", [])),
        "sections": len(rule_index.get("sections", [])),
        "glossary_entries": len(glossary.get("entries", [])),
        "mechanics": len(mechanics.get("mechanics", [])),
        "coverage_statuses": dict(sorted(statuses.items())),
        "conformance_cases": conformance_coverage["total_cases"],
        "semantic_passing_cases": conformance_coverage[
            "semantic_passing_cases"
        ],
        "unreviewed_cases": conformance_coverage["unreviewed_cases"],
    }


def compare_rule_indexes(
    current: Mapping[str, Any],
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    current_by_id = {
        str(row["rule_id"]): row for row in current.get("rules", [])
    }
    previous_by_id = {
        str(row["rule_id"]): row for row in previous.get("rules", [])
    }
    added = sorted(set(current_by_id) - set(previous_by_id))
    removed = sorted(set(previous_by_id) - set(current_by_id))
    changed = sorted(
        rule_id
        for rule_id in set(current_by_id) & set(previous_by_id)
        if current_by_id[rule_id].get("text_sha256")
        != previous_by_id[rule_id].get("text_sha256")
    )
    added_by_hash: dict[str, list[str]] = defaultdict(list)
    removed_by_hash: dict[str, list[str]] = defaultdict(list)
    for rule_id in added:
        added_by_hash[str(current_by_id[rule_id]["text_sha256"])].append(
            rule_id
        )
    for rule_id in removed:
        removed_by_hash[str(previous_by_id[rule_id]["text_sha256"])].append(
            rule_id
        )
    renumbered = [
        {
            "from": old_id,
            "to": new_id,
            "text_sha256": content_hash,
        }
        for content_hash in sorted(
            set(added_by_hash) & set(removed_by_hash)
        )
        for old_id in removed_by_hash[content_hash]
        for new_id in added_by_hash[content_hash]
    ]
    affected = sorted(set(changed) | set(removed))
    return {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "from_effective_date": previous.get("effective_date"),
        "to_effective_date": current.get("effective_date"),
        "from_source_sha256": previous.get("source_sha256"),
        "to_source_sha256": current.get("source_sha256"),
        "added_rule_ids": added,
        "removed_rule_ids": removed,
        "changed_rule_ids": changed,
        "renumbered_rules": renumbered,
        "invalidated_rule_ids": affected,
        "requires_review": bool(added or removed or changed),
    }


def rules_diff(
    root: str | Path,
    *,
    against: str | Path,
) -> dict[str, Any]:
    current = _load_required(root, "rules/rule-index.json")
    previous = _load_required(against, "rules/rule-index.json")
    delta = compare_rule_indexes(current, previous)
    _write_delta(root, delta)
    return delta


def _write_delta(root: str | Path, delta: Mapping[str, Any]) -> None:
    coverage_dir = Path(root) / "coverage"
    _atomic_json(coverage_dir / "rules-delta.json", delta)
    lines = [
        f'---\ntitle: "Comprehensive Rules delta"\nstatus: "generated"\nauthoritative_source: "coverage/rules-delta.json"\nverified: "{delta.get("to_effective_date")}"\naudience: "rules and engine contributors"\nmaintenance: "generated"\n---\n\n# Comprehensive Rules delta',
        "",
        f"- From: `{delta.get('from_effective_date')}`",
        f"- To: `{delta.get('to_effective_date')}`",
        f"- Added: {len(delta.get('added_rule_ids', []))}",
        f"- Removed: {len(delta.get('removed_rule_ids', []))}",
        f"- Changed: {len(delta.get('changed_rule_ids', []))}",
        f"- Renumbered candidates: {len(delta.get('renumbered_rules', []))}",
        f"- Review required: {str(bool(delta.get('requires_review'))).lower()}",
        "",
    ]
    _atomic_text(coverage_dir / "rules-delta.md", "\n".join(lines))


def rules_coverage(root: str | Path) -> dict[str, Any]:
    rule_index = _load_required(root, "rules/rule-index.json")
    mechanics = _load_required(root, "rules/mechanic-index.json")
    conformance = _load_required(
        root, "rules/conformance-cases.json"
    )
    conformance_coverage = rule_conformance_coverage(conformance)
    rules = list(rule_index.get("rules", []))
    status_counts = Counter(
        str(rule.get("coverage_status") or "unclassified")
        for rule in rules
    )
    invalid = sorted(set(status_counts) - COVERAGE_STATUSES)
    trusted = int(status_counts.get("trusted", 0))
    rules_complete = (
        bool(rules)
        and trusted == len(rules)
        and not invalid
    )
    return {
        "schema_version": RULES_CORPUS_SCHEMA_VERSION,
        "effective_date": rule_index.get("effective_date"),
        "source_sha256": rule_index.get("source_sha256"),
        "total_rules": len(rules),
        "status_counts": dict(sorted(status_counts.items())),
        "trusted_rules": trusted,
        "trusted_fraction": (
            round(trusted / len(rules), 6) if rules else 0.0
        ),
        "total_mechanics": len(mechanics.get("mechanics", [])),
        "invalid_statuses": invalid,
        "conformance": conformance_coverage,
        "current_snapshot_complete": rules_complete
        and conformance_coverage["current_snapshot_complete"],
        "conformance_snapshot_complete": conformance_coverage[
            "current_snapshot_complete"
        ],
    }


def _write_coverage(root: str | Path, coverage: Mapping[str, Any]) -> None:
    coverage_dir = Path(root) / "coverage"
    _atomic_json(coverage_dir / "rules-coverage.json", coverage)
    lines = [
        f'---\ntitle: "Comprehensive Rules coverage"\nstatus: "generated"\nauthoritative_source: "coverage/rules-coverage.json"\nverified: "{coverage.get("effective_date")}"\naudience: "rules and engine contributors"\nmaintenance: "generated"\n---\n\n# Comprehensive Rules coverage',
        "",
        f"- Effective date: `{coverage.get('effective_date')}`",
        f"- Source SHA-256: `{coverage.get('source_sha256')}`",
        f"- Indexed rules: {coverage.get('total_rules', 0)}",
        f"- Trusted rules: {coverage.get('trusted_rules', 0)}",
        f"- Trusted fraction: {coverage.get('trusted_fraction', 0.0):.2%}",
        (
            "- Semantic conformance passes: "
            + str(
                coverage.get("conformance", {}).get(
                    "semantic_passing_cases", 0
                )
            )
        ),
        (
            "- Unreviewed conformance cases: "
            + str(
                coverage.get("conformance", {}).get(
                    "unreviewed_cases", 0
                )
            )
        ),
        (
            "- Current snapshot complete: "
            + str(
                bool(coverage.get("current_snapshot_complete"))
            ).lower()
        ),
        "",
        "A green completeness claim is blocked until every behavioral rule "
        "and mechanic contract in the pinned snapshot is trusted.",
        "",
    ]
    _atomic_text(coverage_dir / "rules-coverage.md", "\n".join(lines))


def _write_conformance_coverage(
    root: str | Path,
    coverage: Mapping[str, Any],
) -> None:
    coverage_dir = Path(root) / "coverage"
    _atomic_json(
        coverage_dir / "rules-conformance.json",
        coverage,
    )
    counts = coverage.get("status_counts", {})
    lines = [
        f'---\ntitle: "Comprehensive Rules conformance cases"\nstatus: "generated"\nauthoritative_source: "coverage/rules-conformance.json"\nverified: "{coverage.get("effective_date")}"\naudience: "rules and engine contributors"\nmaintenance: "generated"\n---\n\n# Comprehensive Rules conformance cases',
        "",
        f"- Effective date: `{coverage.get('effective_date')}`",
        f"- Source SHA-256: `{coverage.get('source_sha256')}`",
        f"- Total case records: {coverage.get('total_cases', 0)}",
        (
            "- Executable semantic passes: "
            + str(coverage.get("semantic_passing_cases", 0))
        ),
        (
            "- Executable semantic failures: "
            + str(coverage.get("semantic_failing_cases", 0))
        ),
        f"- Blocked: {coverage.get('blocked_cases', 0)}",
        f"- Skipped: {coverage.get('skipped_cases', 0)}",
        f"- Unreviewed: {coverage.get('unreviewed_cases', 0)}",
        f"- Definition-only: {coverage.get('definition_only_cases', 0)}",
        f"- Inventory-only: {coverage.get('inventory_only_cases', 0)}",
        (
            "- Current snapshot complete: "
            + str(
                bool(coverage.get("current_snapshot_complete"))
            ).lower()
        ),
        "",
        "Status detail:",
        "",
    ]
    lines.extend(
        f"- `{status}`: {count}"
        for status, count in sorted(counts.items())
    )
    lines.extend(
        [
            "",
            "Inventory-only records prove source linkage and case existence; "
            "they do not prove that the engine implements the rule.",
            "",
        ]
    )
    _atomic_text(
        coverage_dir / "rules-conformance.md",
        "\n".join(lines),
    )


def rules_next(
    root: str | Path,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    return rules_next_work(root, limit=limit)


def _mechanic_contract_errors(
    root: Path,
    mechanics_registry: Mapping[str, Any],
    known_rules: set[str],
) -> list[str]:
    errors = []
    try:
        contracts = load_mechanic_contracts(
            root,
            expected_effective_date=str(
                mechanics_registry.get("effective_date") or ""
            ),
            expected_source_sha256=str(
                mechanics_registry.get("source_sha256") or ""
            ),
            known_rule_ids=known_rules,
        )
        expected_contracts = {
            str(contract["mechanic_id"]): contract
            for contract in contracts
        }
        registry_contracts = {
            str(row.get("mechanic_id")): row
            for row in mechanics_registry.get("mechanics", [])
            if row.get("contract_path")
        }
        if set(expected_contracts) != set(registry_contracts):
            errors.append(
                "mechanics/registry.json contract set is stale"
            )
        for mechanic_id, contract in expected_contracts.items():
            row = registry_contracts.get(mechanic_id, {})
            if (
                row.get("contract_path")
                != contract.get("_contract_path")
                or row.get("contract_sha256")
                != contract.get("_contract_sha256")
                or row.get("coverage_status")
                != contract.get("coverage_status")
                or row.get("trust_level")
                != contract.get("trust_level")
            ):
                errors.append(
                    f"Mechanic {mechanic_id} does not match its contract"
                )
    except MechanicContractError as exc:
        errors.append(str(exc))
    return errors


def verify_rules_corpus(
    root: str | Path,
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(root)
    manifest = _load_required(root, "rules/manifest.json")
    rule_index = _load_required(root, "rules/rule-index.json")
    glossary = _load_required(root, "rules/glossary-index.json")
    mechanics = _load_required(root, "rules/mechanic-index.json")
    mechanics_registry = _load_required(
        root, "mechanics/registry.json"
    )
    graph = _load_required(root, "rules/dependency-graph.json")
    conformance = _load_required(
        root, "rules/conformance-cases.json"
    )
    errors: list[str] = []
    source_sha256 = str(manifest.get("source_sha256") or "")

    documents = {
        "rule-index.json": rule_index,
        "glossary-index.json": glossary,
        "mechanic-index.json": mechanics,
        "dependency-graph.json": graph,
        "conformance-cases.json": conformance,
    }
    for name, value in documents.items():
        expected = manifest.get("derived_hashes", {}).get(name)
        if expected != _json_hash(value):
            errors.append(f"{name} does not match its manifest hash")
        if value.get("source_sha256") != source_sha256:
            errors.append(f"{name} points to a different source snapshot")
    expected_mechanics_hash = manifest.get("derived_hashes", {}).get(
        "mechanics/registry.json"
    )
    if expected_mechanics_hash != _json_hash(mechanics_registry):
        errors.append(
            "mechanics/registry.json does not match its manifest hash"
        )
    if mechanics_registry.get("source_sha256") != source_sha256:
        errors.append(
            "mechanics/registry.json points to a different source snapshot"
        )

    rules = list(rule_index.get("rules", []))
    rule_ids = [str(rule.get("rule_id")) for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        errors.append("rule-index.json contains duplicate rule IDs")
    if len(rules) != int(manifest.get("rule_count", -1)):
        errors.append("manifest rule_count does not match rule index")
    known_rules = set(rule_ids)
    for rule in rules:
        if rule.get("coverage_status") not in COVERAGE_STATUSES:
            errors.append(
                f"{rule.get('rule_id')} has an unknown coverage status"
            )
        parent = rule.get("parent_rule_id")
        if parent and parent not in known_rules:
            errors.append(
                f"{rule.get('rule_id')} has missing parent {parent}"
            )
        if rule.get("source_sha256") != source_sha256:
            errors.append(
                f"{rule.get('rule_id')} points to a different source"
            )
    for mechanic in mechanics.get("mechanics", []):
        for rule_id in mechanic.get("rule_references", []):
            if str(rule_id) not in known_rules:
                errors.append(
                    f"Mechanic {mechanic.get('mechanic_id')} references "
                    f"missing rule {rule_id}"
                )
    errors.extend(
        validate_rule_conformance(
            conformance,
            rule_index,
            known_test_ids=discover_unittest_ids(root),
        )
    )
    conformance_reviews, review_errors = (
        load_rule_conformance_reviews(root, rule_index)
    )
    errors.extend(review_errors)
    conformance_by_rule = {
        str(case.get("rule_id")): case
        for case in conformance.get("cases", [])
    }
    if (root / "rules" / "conformance-reviews").is_dir():
        reviewed_rule_ids = {
            rule_id
            for rule_id, case in conformance_by_rule.items()
            if case.get("reviewed") is True
        }
        if reviewed_rule_ids != set(conformance_reviews):
            missing_reviews = sorted(
                reviewed_rule_ids - set(conformance_reviews)
            )
            missing_cases = sorted(
                set(conformance_reviews) - reviewed_rule_ids
            )
            if missing_reviews:
                errors.append(
                    "Generated conformance cases retain reviews absent "
                    "from authoritative overlays: "
                    + ", ".join(missing_reviews[:10])
                )
            if missing_cases:
                errors.append(
                    "Authoritative conformance overlays are absent from "
                    "generated cases: "
                    + ", ".join(missing_cases[:10])
                )
    for rule_id, review in conformance_reviews.items():
        case = conformance_by_rule.get(rule_id, {})
        for field in REVIEW_FIELDS:
            if case.get(field) != review.get(field):
                errors.append(
                    f"Conformance case {rule_id} is not synchronized "
                    f"with its review field {field}"
                )
    mechanic_ids = [
        str(mechanic.get("mechanic_id"))
        for mechanic in mechanics_registry.get("mechanics", [])
    ]
    if len(mechanic_ids) != len(set(mechanic_ids)):
        errors.append("mechanics/registry.json has duplicate mechanic IDs")
    for mechanic in mechanics_registry.get("mechanics", []):
        if mechanic.get("coverage_status") not in COVERAGE_STATUSES:
            errors.append(
                f"Mechanic {mechanic.get('mechanic_id')} has an unknown "
                "coverage status"
            )
    errors.extend(
        _mechanic_contract_errors(root, mechanics_registry, known_rules)
    )

    cache = (
        Path(cache_dir)
        if cache_dir is not None
        else root / "local" / "rules"
    )
    cached_path = cache / str(manifest.get("cached_filename") or "")
    raw_source_verified: bool | None = None
    if cached_path.is_file():
        raw_source_verified = _sha256(cached_path.read_bytes()) == source_sha256
        if not raw_source_verified:
            errors.append("Cached raw rules file does not match source hash")

    coverage = rules_coverage(root)
    if coverage["invalid_statuses"]:
        errors.append("Coverage contains invalid status values")
    scheduler_catalog = root / "platform" / "rules-subsystems.json"
    scheduler_queue = (
        root / "coverage" / "rules-dependency-queue.json"
    )
    if scheduler_catalog.exists() or scheduler_queue.exists():
        errors.extend(rules_dependency_queue_errors(root))
    return {
        "ok": not errors,
        "errors": errors,
        "effective_date": manifest.get("effective_date"),
        "source_sha256": source_sha256,
        "rules_verified": len(rules),
        "mechanics_verified": len(mechanics.get("mechanics", [])),
        "conformance_cases_verified": len(
            conformance.get("cases", [])
        ),
        "raw_source_verified": raw_source_verified,
        "current_snapshot_complete": coverage[
            "current_snapshot_complete"
        ],
    }


def rules_report(root: str | Path) -> str:
    inventory = rules_inventory(root)
    coverage = rules_coverage(root)
    verification = verify_rules_corpus(root)
    return "\n".join(
        [
            "# Rules corpus report",
            "",
            f"- Effective date: `{inventory['effective_date']}`",
            f"- Source SHA-256: `{inventory['source_sha256']}`",
            f"- Parser: `{inventory['parser_version']}`",
            f"- Rules: {inventory['rules']}",
            f"- Sections: {inventory['sections']}",
            f"- Glossary entries: {inventory['glossary_entries']}",
            f"- Mechanics: {inventory['mechanics']}",
            f"- Conformance cases: {inventory['conformance_cases']}",
            (
                "- Executable semantic passes: "
                + str(inventory["semantic_passing_cases"])
            ),
            (
                "- Unreviewed conformance cases: "
                + str(inventory["unreviewed_cases"])
            ),
            f"- Corpus verification: {'pass' if verification['ok'] else 'fail'}",
            (
                "- Pinned-snapshot completeness: "
                + (
                    "pass"
                    if coverage["current_snapshot_complete"]
                    else "blocked"
                )
            ),
            "",
            "The index stores rule IDs, source spans, hashes, and compact "
            "metadata—not the downloaded Comprehensive Rules prose.",
            "",
        ]
    )


def execute_rules_corpus_operation(
    operation: str,
    *,
    root: str | Path,
    cache_dir: str | Path | None = None,
    source_file: str | Path | None = None,
    source_url: str | None = None,
    against: str | Path | None = None,
    limit: int = 20,
    output: str | Path | None = None,
    card_db_path: str | Path | None = None,
) -> dict[str, Any] | str:
    if operation == "sync":
        return sync_rules_corpus(
            root,
            cache_dir=cache_dir,
            source_file=source_file,
            source_url=source_url,
            card_db_path=card_db_path,
        )
    if operation == "inventory":
        return rules_inventory(root)
    if operation == "coverage":
        value = rules_coverage(root)
        _write_coverage(root, value)
        return value
    if operation == "conformance":
        value = rule_conformance_coverage(
            _load_required(root, "rules/conformance-cases.json")
        )
        _write_conformance_coverage(root, value)
        return value
    if operation == "queue":
        return load_rules_dependency_queue(root)
    if operation == "next":
        return rules_next(root, limit=limit)
    if operation == "verify":
        return verify_rules_corpus(root, cache_dir=cache_dir)
    if operation == "diff":
        if against is None:
            raise RulesCorpusError("rules diff requires --against")
        return rules_diff(root, against=against)
    if operation == "report":
        value = rules_report(root)
        if output is not None:
            _atomic_text(Path(output), value)
        return value
    raise RulesCorpusError(f"Unknown rules corpus operation {operation!r}")
