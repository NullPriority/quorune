from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from ..carddb import CardDatabase, CardRecord
from ..rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from ..semantic_runtime import (
    default_semantic_handler_registry,
    describe_runtime_handler,
    runtime_component_inventory,
    runtime_component_registry_fingerprint,
)
from ..semantics import SemanticRegistry
from ..util import stable_json
from .adapters import (
    compile_best_available_card_program,
)
from .model import CardProgram


CARD_PROGRAM_OPERATIONS = {
    "compile",
    "explain",
    "audit",
    "diff",
    "overrides",
    "coverage",
    "trust-closure",
    "runtime-components",
}


def _typed_handler_mapping(
    operations: list[str],
) -> list[dict[str, Any]]:
    registry = default_semantic_handler_registry()
    return [
        descriptor
        for operation in operations
        if (descriptor := registry.describe(operation)) is not None
    ]


def _runtime_handler_mapping(
    effects: list[Mapping[str, Any]],
    *,
    event_handlers: list[dict[str, Any]] | None = None,
    operation_key: str = "effect_operations",
) -> dict[str, Any]:
    operations = [str(effect.get("op") or "") for effect in effects]
    result = {
        operation_key: operations,
        "typed_handlers": _typed_handler_mapping(operations),
    }
    if event_handlers is not None:
        result["event_handlers"] = [
            {
                **handler,
                "registry": describe_runtime_handler(
                    str(handler.get("handler_id") or "")
                ),
            }
            for handler in event_handlers
        ]
    return result


def _compile_best_available(
    db: CardDatabase,
    record: CardRecord,
    *,
    registry: SemanticRegistry,
    profile: str,
    capabilities: CapabilityRegistry | None = None,
) -> CardProgram:
    return compile_best_available_card_program(
        db,
        record,
        semantic_registry=registry,
        capability_registry=capabilities,
        capability_profile=profile,
    )


def explain_card_program(program: CardProgram) -> dict[str, Any]:
    abilities = []
    for ability in program.to_dict()["abilities"]:
        runtime = ability["runtime"]
        abilities.append(
            {
                "ability_id": ability["ability_id"],
                "semantic_key": ability["semantic_key"],
                "face_id": ability["face_id"],
                "kind": ability["kind"],
                "active_zones": ability["active_zones"],
                "timing_permissions": ability["timing_permissions"],
                "costs": ability["costs"],
                "modes": ability["modes"],
                "targets": ability["targets"],
                "choices": ability["choices"],
                "effect_nodes": ability["effect_nodes"],
                "triggers": ability["triggers"],
                "static_effects": ability["static_effects"],
                "replacement_effects": ability["replacement_effects"],
                "prevention_effects": ability["prevention_effects"],
                "continuous_effects": ability["continuous_effects"],
                "linked_ability_ids": ability["linked_ability_ids"],
                "durations": ability["durations"],
                "delayed_effects": ability["delayed_effects"],
                "copy_behavior": ability["copy_behavior"],
                "zone_permissions": ability["zone_permissions"],
                "capabilities": ability["capability_dependencies"],
                "source_span": ability["source_span"],
                "tests": runtime["tests"],
                "trust_level": runtime["trust_level"],
                "trust_blockers": (
                    ability["trust_closure"]["blockers"]
                    if ability["trust_closure"] is not None
                    else []
                ),
                "source": {
                    "authored_by": runtime["provenance"].get("authored_by"),
                    "review_status": runtime["provenance"].get(
                        "review_status"
                    ),
                    "template_id": runtime["provenance"].get("template_id"),
                },
                "runtime_handler_mapping": _runtime_handler_mapping(
                    ability["effect_nodes"],
                    event_handlers=runtime["handlers"],
                ),
            }
        )
    return {
        "schema_version": program.schema_version,
        "card_name": program.card_name,
        "oracle_id": program.oracle_id,
        "fingerprint": program.fingerprint,
        "semantic_hash": program.semantic_hash,
        "compiler_version": program.compiler_version,
        "trusted": program.trust_closure["trusted"],
        "trust_basis": program.trust_closure["trust_basis"],
        "strict_capability_ready": program.trust_closure[
            "strict_capability_ready"
        ],
        "closure_layers": program.trust_closure["closure_layers"],
        "ambient_interaction_surfaces": program.trust_closure[
            "ambient_interaction_surfaces"
        ],
        "compatibility_provenance": program.trust_closure[
            "compatibility_provenance"
        ],
        "trust_blockers": program.trust_closure["blockers"],
        "faces": [face.to_dict() for face in program.faces],
        "abilities": abilities,
        "residuals": list(program.residuals),
        "provenance": program.provenance,
    }


def audit_card_program(program: CardProgram) -> dict[str, Any]:
    restored = CardProgram.from_dict(program.to_dict())
    deterministic_roundtrip = restored.to_dict() == program.to_dict()
    source_blockers = [
        blocker
        for blocker in program.trust_closure["blockers"]
        if "stale_" in blocker
    ]
    return {
        "schema_version": program.schema_version,
        "oracle_id": program.oracle_id,
        "card_name": program.card_name,
        "fingerprint": program.fingerprint,
        "deterministic_roundtrip": deterministic_roundtrip,
        "ability_ids_unique": len(
            {ability.ability_id for ability in program.abilities}
        )
        == len(program.abilities),
        "ability_count": len(program.abilities),
        "residual_count": len(program.residuals),
        "capability_dependencies": list(program.capability_dependencies),
        "trust_closure": program.trust_closure,
        "trust_basis": program.trust_closure["trust_basis"],
        "strict_capability_ready": program.trust_closure[
            "strict_capability_ready"
        ],
        "source_fingerprints_match": not source_blockers,
        "source_fingerprint_blockers": source_blockers,
        "runtime_handler_mapping": {
            ability.ability_id: {
                "semantic_key": ability.key,
                "event": ability.event,
                **_runtime_handler_mapping(
                    ability.effects,
                    event_handlers=ability.handlers,
                    operation_key="operations",
                ),
            }
            for ability in program.abilities
        },
        "structurally_valid": deterministic_roundtrip,
        "trusted_for_runtime": program.trust_closure["trusted"],
        "valid": (
            deterministic_roundtrip
            and program.trust_closure["trusted"]
        ),
    }


def _load_snapshot(path: str | Path, oracle_id: str) -> CardProgram:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("CardProgram snapshot must be an object")
    if isinstance(raw.get("card_programs"), Mapping):
        candidate = raw["card_programs"].get(oracle_id)
        if candidate is None:
            raise ValueError(
                f"Snapshot has no CardProgram for {oracle_id}"
            )
        raw = candidate
    return CardProgram.from_dict(raw)


def _diff_values(
    before: Any,
    after: Any,
    *,
    path: str = "$",
    result: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    changes = result if result is not None else []
    if type(before) is not type(after):
        changes.append({"path": path, "before": before, "after": after})
        return changes
    if isinstance(before, Mapping):
        keys = sorted(set(before) | set(after))
        for key in keys:
            child = f"{path}.{key}"
            if key not in before:
                changes.append(
                    {"path": child, "before": None, "after": after[key]}
                )
            elif key not in after:
                changes.append(
                    {"path": child, "before": before[key], "after": None}
                )
            else:
                _diff_values(
                    before[key], after[key], path=child, result=changes
                )
        return changes
    if isinstance(before, list):
        for index in range(max(len(before), len(after))):
            child = f"{path}[{index}]"
            if index >= len(before):
                changes.append(
                    {"path": child, "before": None, "after": after[index]}
                )
            elif index >= len(after):
                changes.append(
                    {"path": child, "before": before[index], "after": None}
                )
            else:
                _diff_values(
                    before[index], after[index], path=child, result=changes
                )
        return changes
    if before != after:
        changes.append({"path": path, "before": before, "after": after})
    return changes


def diff_card_program(
    current: CardProgram,
    against: CardProgram,
) -> dict[str, Any]:
    changes = _diff_values(against.to_dict(), current.to_dict())
    return {
        "schema_version": 1,
        "oracle_id": current.oracle_id,
        "against_fingerprint": against.fingerprint,
        "current_fingerprint": current.fingerprint,
        "changed": bool(changes),
        "change_count": len(changes),
        "changes": changes,
    }


def semantic_overrides(registry: SemanticRegistry) -> dict[str, Any]:
    rows = []
    for program in registry.programs():
        authored_by = str(program.provenance.get("authored_by") or "")
        review_status = str(program.provenance.get("review_status") or "")
        override_scope = program.provenance.get("override_scope")
        if (
            override_scope is None
            and "override" not in authored_by.casefold()
            and "override" not in review_status.casefold()
        ):
            continue
        rows.append(
            {
                "oracle_id": program.oracle_id,
                "ability_id": program.ability_id,
                "semantic_key": program.key,
                "authored_by": authored_by,
                "review_status": review_status,
                "override_scope": override_scope,
                "source_oracle_hash": program.provenance.get(
                    "source_oracle_hash"
                ),
            }
        )
    return {
        "schema_version": 1,
        "typed_override_count": len(rows),
        "overrides": rows,
        "implicit_semantic_pack_programs": len(registry.programs()),
        "boundary": (
            "Only explicit typed override metadata is classified as an "
            "override; reviewed semantic packs remain compatibility inputs."
        ),
    }


class _CardProgramCoverageCollector:
    """Canonical CardProgram counters for one ordered corpus scope."""

    def __init__(self, capabilities, profile):
        self.capabilities, self.profile = capabilities, profile
        self.statuses, self.trust_bases = Counter(), Counter()
        self.ability_count = self.residual_count = 0
        self.failures = []

    def observe(self, record, program=None, *, error=None):
        statuses, trust_bases, failures = self.statuses, self.trust_bases, self.failures
        if error is not None:
            statuses["failed"] += 1
            if len(failures) < 50:
                failures.append(
                    {
                        "oracle_id": record.oracle_id,
                        "card_name": record.name,
                        "error": str(error),
                    }
                )
            return
        self.ability_count += len(program.abilities)
        self.residual_count += len(program.residuals)
        trust_bases[program.trust_closure["trust_basis"]] += 1
        if program.trust_closure["trusted"]:
            statuses["trusted"] += 1
        elif program.residuals:
            statuses["residual"] += 1
        else:
            statuses["untrusted"] += 1

    def report(self, db, *, commander_legal_only, limit):
        from ..oracle_ir import ORACLE_COMPILER_VERSION
        capabilities, profile = self.capabilities, self.profile
        statuses, trust_bases, failures = self.statuses, self.trust_bases, self.failures
        ability_count, residual_count = self.ability_count, self.residual_count
        total = sum(statuses.values())
        metadata = db.metadata()
        return {
            "schema_version": 1,
            "card_program_schema_version": 2,
            "compiler_version": ORACLE_COMPILER_VERSION,
            "profile": profile,
            "capability_registry_fingerprint": capabilities.fingerprint,
            "capability_evidence_fingerprint": (
                capabilities.evidence_fingerprint
            ),
            "card_data_snapshot": {
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
            },
            "commander_legal_only": commander_legal_only,
            "limited": limit is not None,
            "cards_considered": total,
            "ability_programs": ability_count,
            "material_residuals": residual_count,
            "status_counts": dict(sorted(statuses.items())),
            "trust_basis_counts": dict(sorted(trust_bases.items())),
            "failures": failures,
            "current_snapshot_complete": (
                total > 0
                and statuses.get("trusted", 0) == total
                and not residual_count
                and not failures
            ),
        }


def card_program_coverage(
    db: CardDatabase,
    *,
    registry: SemanticRegistry,
    profile: str,
    commander_legal_only: bool,
    limit: int | None,
) -> dict[str, Any]:
    capabilities = load_default_capability_registry()
    collector = _CardProgramCoverageCollector(capabilities, profile)
    for record in db.iter_cards(commander_legal_only=commander_legal_only, limit=limit):
        try:
            program = _compile_best_available(db, record, registry=registry,
                                              profile=profile, capabilities=capabilities)
        except (KeyError, ValueError) as exc:
            collector.observe(record, error=exc)
        else:
            collector.observe(record, program)
    return collector.report(db, commander_legal_only=commander_legal_only, limit=limit)


def runtime_component_status(profile: str) -> dict[str, Any]:
    capabilities = load_default_capability_registry()
    semantic_registry = default_semantic_handler_registry()

    def bind(row: Mapping[str, Any]) -> dict[str, Any]:
        closure = capabilities.closure(
            row["capability_dependencies"], profile=profile
        )
        return {**dict(row), "capability_closure": closure.to_dict()}

    components = [bind(row) for row in runtime_component_inventory()]
    semantic_handlers = [
        bind(row) for row in semantic_registry.inventory()
    ]
    return {
        "schema_version": 1,
        "profile": profile,
        "capability_registry_fingerprint": capabilities.fingerprint,
        "capability_evidence_fingerprint": capabilities.evidence_fingerprint,
        "semantic_handler_registry_fingerprint": semantic_registry.fingerprint,
        "runtime_component_registry_fingerprint": (
            runtime_component_registry_fingerprint()
        ),
        "semantic_handlers": semantic_handlers,
        "runtime_components": components,
        "strict_capability_ready": all(
            row["capability_closure"]["trusted"]
            for row in [*semantic_handlers, *components]
        ),
    }


def execute_card_operation(
    operation: str,
    *,
    db_path: str | Path,
    card: str | None = None,
    profile: str = "traditional",
    against: str | Path | None = None,
    commander_legal_only: bool = False,
    limit: int | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    if operation not in CARD_PROGRAM_OPERATIONS:
        raise ValueError(f"Unknown card operation {operation!r}")
    registry = SemanticRegistry()
    if operation == "overrides":
        value = semantic_overrides(registry)
    elif operation == "runtime-components":
        value = runtime_component_status(profile)
    else:
        with CardDatabase(db_path) as db:
            if operation == "coverage":
                value = card_program_coverage(
                    db,
                    registry=registry,
                    profile=profile,
                    commander_legal_only=commander_legal_only,
                    limit=limit,
                )
            else:
                if not card:
                    raise ValueError(f"card {operation} requires a card name")
                program = _compile_best_available(
                    db, db.lookup(card), registry=registry, profile=profile
                )
                if operation == "compile":
                    value = program.to_dict()
                elif operation == "explain":
                    value = explain_card_program(program)
                elif operation == "audit":
                    value = audit_card_program(program)
                elif operation == "trust-closure":
                    value = {
                        "schema_version": program.schema_version,
                        "oracle_id": program.oracle_id,
                        "card_name": program.card_name,
                        "program_fingerprint": program.fingerprint,
                        "trust_closure": program.trust_closure,
                    }
                else:
                    if against is None:
                        raise ValueError("card diff requires --against")
                    value = diff_card_program(
                        program,
                        _load_snapshot(against, program.oracle_id),
                    )
    if output is not None:
        Path(output).write_text(stable_json(value), encoding="utf-8")
    return value
