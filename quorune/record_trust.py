from __future__ import annotations

import copy
from typing import Any, Callable, Iterable, Mapping

from .card_programs import semantic_program_execution_provenance
from .rules.capabilities import load_default_capability_registry
from .semantic_runtime import (
    default_semantic_handler_registry,
    runtime_component_inventory,
    runtime_component_registry_fingerprint,
)
from .semantics import SemanticRegistry
from .stack_resolution import trusted_generic_empty_resolution


def resolved_semantic_execution_provenance_rows(
    engine: Any, *, events: Iterable[Any], before_event_sequence: int,
    before_stack_items: Mapping[str, Any], capability_profile: str,
) -> list[dict[str, Any]]:
    """Bind actual resolution rows with fresh inputs only when they are needed."""
    rows = []
    capabilities = None
    for event in events:
        if event.event_id <= before_event_sequence or event.code != "stack.resolve":
            continue
        stack_ref = str(event.details.get("stack") or "")
        item = before_stack_items.get(stack_ref)
        if item is None or not item.semantic_key:
            continue
        if capabilities is None:
            capabilities = load_default_capability_registry()
        rows.append(semantic_execution_provenance_row(
            engine, item.semantic_key, capability_registry=capabilities,
            profile=capability_profile, stack_item=item,
        ))
    return rows


def card_program_trust_provenance(
    semantics: SemanticRegistry,
) -> dict[str, dict[str, Any]]:
    return {
        program.oracle_id: {
            "fingerprint": program.fingerprint,
            "trust_basis": program.trust_closure["trust_basis"],
            "trust_closure_fingerprint": program.trust_closure[
                "fingerprint"
            ],
            "strict_capability_ready": program.trust_closure[
                "strict_capability_ready"
            ],
        }
        for program in semantics.card_programs()
    }


def runtime_trust_provenance() -> dict[str, Any]:
    capabilities = load_default_capability_registry()
    semantic_handlers = default_semantic_handler_registry()
    return {
        "schema_version": 1,
        "capability_registry_fingerprint": capabilities.fingerprint,
        "capability_evidence_fingerprint": capabilities.evidence_fingerprint,
        "semantic_handler_registry_fingerprint": semantic_handlers.fingerprint,
        "semantic_handler_inventory": semantic_handlers.inventory(),
        "runtime_component_registry_fingerprint": (
            runtime_component_registry_fingerprint()
        ),
        "runtime_component_inventory": runtime_component_inventory(),
    }


def implicit_semantic_execution_provenance(
    engine: Any,
    semantic_key: str,
    *,
    stack_item: Any | None = None,
) -> dict[str, Any] | None:
    """Describe a core generic spell fallback without inventing a program."""

    candidates = (
        (stack_item,)
        if stack_item is not None
        else tuple(reversed(engine.state.stack))
    )
    for item in candidates:
        if item is None or item.semantic_key != semantic_key:
            continue
        if item.kind != "spell" or not item.card_object_id:
            continue
        record = engine.card_record(item.card_object_id)
        if record is None:
            continue
        expected_key = f"{record.oracle_id}:spell:front"
        if semantic_key != expected_key:
            continue
        resolution = trusted_generic_empty_resolution(engine, item, None)
        if resolution is None:
            continue
        return {
            "oracle_id": record.oracle_id,
            "version": 1,
            "builtin": False,
            "implicit_fallback": resolution.provenance,
        }
    return None


def semantic_execution_provenance_row(
    engine: Any,
    semantic_key: str,
    *,
    capability_registry: Any,
    profile: str,
    stack_item: Any | None = None,
) -> dict[str, Any]:
    """Record one resolved registered program or trusted core fallback."""

    program = engine.semantics.get(semantic_key)
    if program is not None:
        card_program = (
            engine.semantics.card_program_for_oracle(program.oracle_id)
            if program.oracle_id
            else None
        )
        return {
            "key": semantic_key,
            "oracle_id": program.oracle_id,
            "version": program.version,
            "builtin": False,
            **semantic_program_execution_provenance(
                program,
                card_program,
                capability_registry=capability_registry,
                profile=profile,
            ),
        }
    implicit = implicit_semantic_execution_provenance(
        engine,
        semantic_key,
        stack_item=stack_item,
    )
    if implicit is not None:
        return {"key": semantic_key, **implicit}
    return {
        "key": semantic_key,
        "oracle_id": None,
        "version": 1 if semantic_key.startswith("builtin:") else None,
        "builtin": semantic_key.startswith("builtin:"),
    }


def _validate_card_program_fingerprints(
    recorded: Any,
    semantics: SemanticRegistry,
    *,
    context: str,
) -> None:
    if recorded is None:
        return
    if not isinstance(recorded, Mapping):
        raise ValueError(f"{context} CardProgram fingerprints are malformed")
    normalized = {str(key): str(value) for key, value in recorded.items()}
    if normalized != semantics.card_program_fingerprints():
        raise ValueError(f"CardProgram fingerprint mismatch in {context}")


def _validate_card_program_trust(
    recorded: Any,
    semantics: SemanticRegistry,
    *,
    context: str,
) -> None:
    # Game Record v3 predates additive CardProgram trust provenance. Preserve
    # replay compatibility for records written before the field existed.
    if recorded is None:
        return
    if not isinstance(recorded, Mapping):
        raise ValueError(f"{context} CardProgram trust provenance is malformed")
    normalized = {
        str(key): copy.deepcopy(dict(value))
        for key, value in recorded.items()
        if isinstance(value, Mapping)
    }
    if len(normalized) != len(recorded):
        raise ValueError(f"{context} CardProgram trust provenance is malformed")
    if normalized != card_program_trust_provenance(semantics):
        raise ValueError(f"CardProgram trust provenance mismatch in {context}")


def _validate_runtime_trust_provenance(
    recorded: Any,
    *,
    context: str,
) -> None:
    # This is additive within Game Record v3; historic records remain replayable
    # while any record that declares the field is bound exactly to this runtime.
    if recorded is None:
        return
    if not isinstance(recorded, Mapping):
        raise ValueError(f"{context} runtime trust provenance is malformed")
    if copy.deepcopy(dict(recorded)) != runtime_trust_provenance():
        raise ValueError(f"Runtime trust provenance mismatch in {context}")


def validate_manifest_runtime_provenance(
    manifest: Mapping[str, Any],
    semantics: SemanticRegistry,
) -> None:
    section = manifest.get("card_programs")
    if section is not None and not isinstance(section, Mapping):
        raise ValueError("Record manifest CardProgram section is malformed")
    _validate_card_program_fingerprints(
        section.get("fingerprints") if isinstance(section, Mapping) else None,
        semantics,
        context="record manifest",
    )
    _validate_card_program_trust(
        section.get("trust") if isinstance(section, Mapping) else None,
        semantics,
        context="record manifest",
    )
    _validate_runtime_trust_provenance(
        manifest.get("runtime_trust"),
        context="record manifest",
    )


def validate_programs_used_provenance(
    recorded: Any,
    semantics: SemanticRegistry,
    *,
    profile: str,
    require_runtime_provenance: bool,
    sequence: Any,
    implicit_provenance: (
        Callable[[str], Mapping[str, Any] | None] | None
    ) = None,
) -> None:
    if not isinstance(recorded, list):
        raise ValueError(f"Malformed semantic provenance at command {sequence}")
    runtime_fields = {
        "card_program_fingerprint",
        "card_program_trust_basis",
        "card_program_trust_closure_fingerprint",
        "legacy_compatibility",
        "runtime_binding_fingerprint",
        "semantic_handler_ids",
        "runtime_component_ids",
        "runtime_capability_closure_fingerprint",
    }
    capabilities = load_default_capability_registry()
    for raw in recorded:
        if not isinstance(raw, Mapping) or not raw.get("key"):
            raise ValueError(
                f"Malformed semantic program provenance at command {sequence}"
            )
        row = dict(raw)
        key = str(row["key"])
        program = semantics.get(key)
        if program is None:
            if key.startswith("builtin:"):
                expected_base = {
                    "oracle_id": None,
                    "version": 1,
                    "builtin": True,
                }
            else:
                implicit = (
                    implicit_provenance(key)
                    if implicit_provenance is not None
                    else None
                )
                if implicit is None:
                    if require_runtime_provenance:
                        raise ValueError(
                            "Unknown semantic program "
                            f"{key!r} at command {sequence}"
                        )
                    # Historic Game Record v3 rows predate explicit generic
                    # fallback provenance. Preserve their prior behavior when
                    # the additive runtime-trust section is absent.
                    continue
                expected_base = dict(implicit)
            expected_runtime: dict[str, Any] = {}
        else:
            card_program = (
                semantics.card_program_for_oracle(program.oracle_id)
                if program.oracle_id
                else None
            )
            expected_base = {
                "oracle_id": program.oracle_id,
                "version": program.version,
                "builtin": False,
            }
            expected_runtime = semantic_program_execution_provenance(
                program,
                card_program,
                capability_registry=capabilities,
                profile=profile,
            )
        for field, expected in expected_base.items():
            if row.get(field) != expected:
                raise ValueError(
                    f"Semantic program provenance mismatch for {field} at "
                    f"command {sequence}"
                )
        if require_runtime_provenance and program is not None:
            missing = sorted(runtime_fields - set(row))
            if missing:
                raise ValueError(
                    "Runtime binding provenance is incomplete at command "
                    f"{sequence}: {', '.join(missing)}"
                )
        for field, expected in expected_runtime.items():
            if field in row and row[field] != expected:
                raise ValueError(
                    f"Runtime binding provenance mismatch for {field} at "
                    f"command {sequence}"
                )


def rebase_command_semantics_provenance(
    rows: Iterable[Mapping[str, Any]],
    registry: SemanticRegistry,
    *,
    registry_fingerprint: str,
    capability_profile: str,
) -> list[dict[str, Any]]:
    capabilities = load_default_capability_registry()
    result: list[dict[str, Any]] = []
    for raw_row in rows:
        row = copy.deepcopy(dict(raw_row))
        row["semantics_fingerprint"] = registry_fingerprint
        semantics = dict(row.get("semantics") or {})
        semantics["registry_hash"] = registry_fingerprint
        programs_used: list[dict[str, Any]] = []
        for raw in semantics.get("programs_used", []):
            if not isinstance(raw, Mapping) or not raw.get("key"):
                continue
            program_row = dict(raw)
            semantic_key = str(program_row["key"])
            program = registry.get(semantic_key)
            if program is not None:
                card_program = (
                    registry.card_program_for_oracle(program.oracle_id)
                    if program.oracle_id
                    else None
                )
                program_row.update(
                    {
                        "oracle_id": program.oracle_id,
                        "version": program.version,
                        "builtin": False,
                        **semantic_program_execution_provenance(
                            program,
                            card_program,
                            capability_registry=capabilities,
                            profile=capability_profile,
                        ),
                    }
                )
            elif semantic_key.startswith("builtin:"):
                program_row.update(
                    {"oracle_id": None, "version": 1, "builtin": True}
                )
            programs_used.append(program_row)
        semantics["programs_used"] = programs_used
        semantics["card_program_schema_version"] = 2
        semantics["card_programs_used"] = (
            registry.card_program_fingerprints_for_keys(
                str(value["key"]) for value in programs_used
            )
        )
        row["semantics"] = semantics
        result.append(row)
    return result
