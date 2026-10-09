from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..carddb import CardDatabase
from ..util import stable_json
from .target_effect_corpus_assurance import TargetEffectCorpusCollector

if TYPE_CHECKING:
    from ..oracle_ir import OracleCardIR
    from ..rules.capabilities import CapabilityRegistry


_ORACLE_COMMAND = "oracle"
_REASON_FIELD = "reason"
_STATUS_FIELD = "status"


class _OracleCoverageCollector:
    """Canonical Oracle counters for one ordered corpus scope."""

    def __init__(self, *, capability_registry, capability_profile,
                 residual_limit=100, include_residual_text=False):
        self.capability_registry = capability_registry
        self.capability_profile = capability_profile
        self.residual_limit = residual_limit
        self.include_residual_text = include_residual_text
        self.statuses = Counter()
        self.templates = Counter()
        self.residual_kinds = Counter()
        self.examples = []
        self.total_faces = self.total_residuals = 0
        self.target_effect_assurance = TargetEffectCorpusCollector() if capability_registry is not None else None

    def observe(self, record, ir):
        target_effect_assurance = self.target_effect_assurance
        statuses, templates, residual_kinds = self.statuses, self.templates, self.residual_kinds
        examples = self.examples
        residual_limit, include_residual_text = self.residual_limit, self.include_residual_text
        if target_effect_assurance is not None:
            target_effect_assurance.observe(record, ir)
        statuses[ir.status] += 1
        self.total_faces += len(ir.faces)
        for face in ir.faces:
            for node in face.nodes:
                if node.template_id:
                    templates[node.template_id] += 1
            for residual in face.residuals:
                if not residual.material:
                    continue
                self.total_residuals += 1
                residual_kinds[residual.kind] += 1
                if len(examples) < residual_limit:
                    example = {
                        "oracle_id": record.oracle_id,
                        "card_name": record.name,
                        "face": face.face_name,
                        "residual_id": residual.residual_id,
                        "kind": residual.kind,
                        "span": asdict(residual.span),
                        _REASON_FIELD: residual.reason,
                        "blockers": list(residual.blockers),
                        "text_sha256": hashlib.sha256(
                            residual.text.encode("utf-8")
                        ).hexdigest(),
                    }
                    if include_residual_text:
                        example["text"] = residual.text
                    examples.append(example)

    def report(self, db, *, commander_legal_only=False, limit=None):
        from ..oracle_ir import ORACLE_COMPILER_VERSION, ORACLE_IR_SCHEMA_VERSION
        statuses, templates, residual_kinds = self.statuses, self.templates, self.residual_kinds
        examples, total_faces, total_residuals = self.examples, self.total_faces, self.total_residuals
        target_effect_assurance = self.target_effect_assurance
        capability_registry, capability_profile = self.capability_registry, self.capability_profile
        total_cards = sum(statuses.values())
        metadata = db.metadata()
        card_data_snapshot = {
            key: metadata.get(key)
            for key in (
                "schema_version",
                "card_count",
                "ruling_count",
                "oracle_source_sha256",
                "rulings_source_sha256",
                "scryfall_oracle_updated_at",
                "scryfall_rulings_updated_at",
            )
            if metadata.get(key) is not None
        }
        return {
            "schema_version": ORACLE_IR_SCHEMA_VERSION,
            "compiler_version": ORACLE_COMPILER_VERSION,
            "capability_profile": (
                capability_profile if capability_registry is not None else None
            ),
            "capability_registry_fingerprint": (
                capability_registry.fingerprint
                if capability_registry is not None
                else None
            ),
            "capability_evidence_fingerprint": (
                capability_registry.evidence_fingerprint
                if capability_registry is not None
                else None
            ),
            "card_data_snapshot": card_data_snapshot,
            "commander_legal_only": commander_legal_only,
            "limited": limit is not None,
            "total_oracle_ids": total_cards,
            "total_faces": total_faces,
            "status_counts": dict(sorted(statuses.items())),
            "exact_fraction": (
                round(statuses.get("exact", 0) / total_cards, 6)
                if total_cards
                else 0.0
            ),
            "material_residuals": total_residuals,
            "residual_kinds": dict(residual_kinds.most_common()),
            "templates": dict(templates.most_common()),
            "residual_examples": examples,
            "target_effect_corpus_assurance": (
                target_effect_assurance.report(
                    compiler_version=ORACLE_COMPILER_VERSION,
                    capability_registry=capability_registry,
                    capability_profile=capability_profile,
                    card_data_snapshot=card_data_snapshot,
                    commander_legal_only=commander_legal_only,
                )
                if target_effect_assurance is not None
                else None
            ),
            "current_snapshot_complete": bool(total_cards)
            and statuses.get("exact", 0) == total_cards
            and total_residuals == 0,
        }


def oracle_corpus_coverage(
    db: CardDatabase,
    *,
    commander_legal_only: bool = False,
    limit: int | None = None,
    residual_limit: int = 100,
    include_residual_text: bool = False,
    capability_registry: CapabilityRegistry | None = None,
    capability_profile: str = "traditional",
) -> dict[str, Any]:
    from ..oracle_ir import compile_oracle_card

    collector = _OracleCoverageCollector(
        capability_registry=capability_registry, capability_profile=capability_profile,
        residual_limit=residual_limit, include_residual_text=include_residual_text,
    )
    for record in db.iter_cards(commander_legal_only=commander_legal_only, limit=limit):
        ir = compile_oracle_card(record, capability_registry=capability_registry,
                                 capability_profile=capability_profile)
        collector.observe(record, ir)
    return collector.report(db, commander_legal_only=commander_legal_only, limit=limit)


def explain_oracle_ir(ir: OracleCardIR) -> dict[str, Any]:
    return {
        "card_name": ir.card_name,
        "oracle_id": ir.oracle_id,
        _STATUS_FIELD: ir.status,
        "compiler_version": ir.compiler_version,
        "semantic_hash": ir.semantic_hash,
        "summary": [
            {
                "face": face.face_name,
                "exact": face.exact,
                "nodes": [
                    {
                        "kind": node.kind,
                        "template_id": node.template_id,
                        "exact": node.exact,
                        "lowerable": node.lowerable,
                        "source_line": node.span.line,
                        "mechanics": list(node.mechanics),
                    }
                    for node in face.nodes
                ],
                "material_residuals": [
                    {
                        "kind": residual.kind,
                        _REASON_FIELD: residual.reason,
                        "source_line": residual.span.line,
                        "blockers": list(residual.blockers),
                    }
                    for residual in face.residuals
                    if residual.material
                ],
            }
            for face in ir.faces
        ],
        "fail_closed": bool(ir.material_residuals),
    }


def execute_oracle_operation(
    operation: str,
    *,
    db_path: str | Path,
    card: str | None = None,
    commander_legal_only: bool = False,
    limit: int | None = None,
    output: str | Path | None = None,
    capability_profile: str | None = None,
) -> dict[str, Any]:
    from ..oracle_ir import ORACLE_OPERATIONS, compile_oracle_card

    if operation not in ORACLE_OPERATIONS:
        raise ValueError(
            f"Unknown {_ORACLE_COMMAND} operation {operation!r}"
        )
    capability_registry = None
    if capability_profile is not None:
        from ..rules.capabilities import load_default_capability_registry

        capability_registry = load_default_capability_registry()
    with CardDatabase(db_path) as db:
        if operation in {"parse", "explain"}:
            if not card:
                raise ValueError(
                    f"{_ORACLE_COMMAND} {operation} requires a card name"
                )
            ir = compile_oracle_card(
                db.lookup(card),
                capability_registry=capability_registry,
                capability_profile=capability_profile or "traditional",
            )
            value = (
                ir.to_dict()
                if operation == "parse"
                else explain_oracle_ir(ir)
            )
        else:
            value = oracle_corpus_coverage(
                db,
                commander_legal_only=commander_legal_only,
                limit=limit,
                residual_limit=(100 if operation == "residuals" else 20),
                include_residual_text=operation == "residuals",
                capability_registry=capability_registry,
                capability_profile=capability_profile or "traditional",
            )
            if operation == "residuals":
                value = {
                    "schema_version": value["schema_version"],
                    "compiler_version": value["compiler_version"],
                    "capability_profile": value["capability_profile"],
                    "capability_registry_fingerprint": value[
                        "capability_registry_fingerprint"
                    ],
                    "capability_evidence_fingerprint": value[
                        "capability_evidence_fingerprint"
                    ],
                    "total_oracle_ids": value["total_oracle_ids"],
                    "material_residuals": value["material_residuals"],
                    "residual_kinds": value["residual_kinds"],
                    "residual_examples": value["residual_examples"],
                }
    if output is not None:
        Path(output).write_text(stable_json(value), encoding="utf-8")
    return value
