from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ..semantic_runtime import (
    default_semantic_handler_registry,
    runtime_component_inventory,
    runtime_component_registry_fingerprint,
)
from ..util import stable_json
from ..semantic_runtime.activated_abilities import (
    is_structural_activated_ability_catalog_program,
    activated_abilities_from_descriptors,
)


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def bind_semantic_program_runtime(
    program: Any,
    *,
    capability_registry: Any,
    profile: str,
) -> dict[str, Any]:
    """Bind one ability to exact registered handlers and capability closure."""

    semantic_handlers = default_semantic_handler_registry()
    component_handlers = {
        row["handler_id"]: row for row in runtime_component_inventory()
    }
    semantic_identities: list[dict[str, Any]] = []
    component_identities: list[dict[str, Any]] = []
    required: set[str] = set()
    blockers: set[str] = set()
    unregistered_operations: set[str] = set()
    for effect in program.effects:
        operation = str(effect.get("op") or "")
        descriptor = semantic_handlers.describe(operation)
        if operation == "fixed_self_counter_keyword_action" and effect.get("action") in {"fading", "vanishing_upkeep", "vanishing_sacrifice"}:
            from ..rules.node_capability_shapes import fixed_self_counter_keyword_action_node_capabilities
            variant = fixed_self_counter_keyword_action_node_capabilities(
                effects=(effect,), target_schema=program.target_schema,
                mechanic_ids=program.coverage,
            )
            if not variant:
                blockers.add("runtime_effect:invalid_fading_action" if effect.get("action") == "fading" else "runtime_effect:invalid_vanishing_action")
            required.update(variant)
        if descriptor is None:
            unregistered_operations.add(operation)
            continue
        semantic_identities.append(descriptor)
        required.update(descriptor["capability_dependencies"])
    for descriptor in program.handlers:
        handler_id = str(descriptor.get("handler_id") or "")
        registered = component_handlers.get(handler_id)
        if registered is None:
            # SemanticProgram construction already rejects this. Retain the
            # fail-closed blocker for callers binding untrusted objects.
            blockers.add(f"runtime_handler:unregistered:{handler_id}")
            continue
        component_identities.append(registered)
        required.update(registered["capability_dependencies"])
        if handler_id == 'activation.catalog.pinned.v1' and not is_structural_activated_ability_catalog_program(program) and descriptor.get('ability',{}).get('power_up') is not None:
            from ..power_up_model import POWER_UP_CAPABILITY
            required.add(POWER_UP_CAPABILITY)
        if descriptor.get("schema_version") != registered["schema_version"]:
            blockers.add(f"runtime_handler:schema_mismatch:{handler_id}")
        if descriptor.get("event") != registered["event"]:
            blockers.add(f"runtime_handler:event_mismatch:{handler_id}")
    declared = set(program.capability_dependencies)
    for dependency in sorted(required - declared):
        blockers.add(f"capability:undeclared_runtime_dependency:{dependency}")
    structural_catalog = is_structural_activated_ability_catalog_program(program)
    if structural_catalog:
        try:
            activated_abilities_from_descriptors(program.handlers)
        except (TypeError, ValueError) as exc:
            blockers.add(f"runtime_handler:invalid_catalog:{exc}")
    closure = program.capability_closure
    current_closure: Mapping[str, Any] | None = None
    if declared:
        current_closure = capability_registry.closure(
            declared, profile=profile
        ).to_dict()
        if closure != current_closure:
            blockers.add("capability:closure_binding_mismatch")
        if current_closure.get("trusted") is not True:
            blockers.add("capability:closure_untrusted")
    elif closure is not None:
        blockers.add("capability:unexpected_closure")
    compatibility = (
        closure is None
        and not declared
        and program.trust_level in {"trusted", "intentionally_ignored"}
        and program.provenance.get("review_status") == "reviewed"
        and bool(program.tests)
    )
    if required and compatibility:
        blockers.add("capability:legacy_runtime_dependencies_unbound")
    result = {
        "ability_id": program.ability_id,
        "semantic_key": program.key,
        "profile": profile,
        "capability_registry_fingerprint": capability_registry.fingerprint,
        "capability_evidence_fingerprint": (
            capability_registry.evidence_fingerprint
        ),
        "semantic_handler_registry_fingerprint": semantic_handlers.fingerprint,
        "runtime_component_registry_fingerprint": (
            runtime_component_registry_fingerprint()
        ),
        "declared_capabilities": sorted(declared),
        "required_registered_capabilities": sorted(required),
        "semantic_handlers": sorted(
            semantic_identities, key=lambda row: row["handler_id"]
        ),
        "runtime_components": sorted(
            component_identities, key=lambda row: row["handler_id"]
        ),
        "unregistered_legacy_operations": sorted(
            value for value in unregistered_operations if value
        ),
        "capability_closure": (
            dict(current_closure) if current_closure is not None else None
        ),
        "compatibility_path": compatibility,
        "blockers": sorted(blockers),
        "strict": not blockers and (closure is not None or structural_catalog),
    }
    result["fingerprint"] = _hash(result)
    return result


def bind_card_program_runtime(
    card_program: Any,
    *,
    capability_registry: Any,
    profile: str,
) -> dict[str, Any]:
    ability_bindings = [
        bind_semantic_program_runtime(
            ability,
            capability_registry=capability_registry,
            profile=profile,
        )
        for ability in card_program.abilities
    ]
    blockers = {
        f"ability:{binding['ability_id']}:{blocker}"
        for binding in ability_bindings
        for blocker in binding["blockers"]
    }
    basis = str(card_program.trust_closure.get("trust_basis") or "unresolved")
    if basis in {"provisional", "unresolved"}:
        blockers.add(f"trust_basis:{basis}")
    compatibility = card_program.trust_closure.get(
        "compatibility_provenance", []
    )
    if basis in {"legacy_reviewed", "mixed"} and not compatibility:
        blockers.add("compatibility_provenance:missing")
    result = {
        "card_program_fingerprint": card_program.fingerprint,
        "oracle_id": card_program.oracle_id,
        "profile": profile,
        "trust_basis": basis,
        "ability_bindings": ability_bindings,
        "compatibility_provenance": compatibility,
        "blockers": sorted(blockers),
        "strict_capability_ready": (
            not blockers
            and basis == "capability_closed"
            and all(binding["strict"] for binding in ability_bindings)
        ),
        "compatible_ready": (
            basis
            in {
                "capability_closed",
                "legacy_reviewed",
                "mixed",
                "non_rules_governed",
            }
            and not any(
                blocker.startswith("trust_basis:")
                or blocker == "compatibility_provenance:missing"
                for blocker in blockers
            )
        ),
    }
    result["fingerprint"] = _hash(result)
    return result


def semantic_program_execution_provenance(
    program: Any,
    card_program: Any | None,
    *,
    capability_registry: Any,
    profile: str,
) -> dict[str, Any]:
    """Return the compact, replay-verifiable runtime binding for one ability."""

    binding = bind_semantic_program_runtime(
        program,
        capability_registry=capability_registry,
        profile=profile,
    )
    closure = binding.get("capability_closure")
    return {
        "card_program_fingerprint": (
            card_program.fingerprint if card_program is not None else None
        ),
        "card_program_trust_basis": (
            card_program.trust_closure["trust_basis"]
            if card_program is not None
            else None
        ),
        "card_program_trust_closure_fingerprint": (
            card_program.trust_closure["fingerprint"]
            if card_program is not None
            else None
        ),
        "legacy_compatibility": (
            card_program is not None
            and card_program.trust_closure["trust_basis"]
            in {"legacy_reviewed", "mixed"}
        ),
        "runtime_binding_fingerprint": binding["fingerprint"],
        "semantic_handler_ids": sorted(
            row["handler_id"] for row in binding["semantic_handlers"]
        ),
        "runtime_component_ids": sorted(
            row["handler_id"] for row in binding["runtime_components"]
        ),
        "runtime_capability_closure_fingerprint": (
            closure.get("fingerprint")
            if isinstance(closure, Mapping)
            else None
        ),
    }
