from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from ..semantic_runtime.activated_abilities import (
    is_structural_activated_ability_catalog_program,
)
from ..util import stable_json


TRUST_BASES = {
    "capability_closed",
    "legacy_reviewed",
    "mixed",
    "provisional",
    "unresolved",
    "non_rules_governed",
}
FORMAT_PROFILE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "traditional": (),
    "commander_duel": (),
    "commander_review": (),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _layer(
    *,
    kind: str,
    status: str,
    direct: Iterable[str] = (),
    reachable: Iterable[str] = (),
    profiles: Iterable[str] = (),
    registry_fingerprints: Iterable[str] = (),
    evidence_fingerprints: Iterable[str] = (),
    blockers: Iterable[str] = (),
    trusted: bool,
) -> dict[str, Any]:
    value = {
        "kind": kind,
        "status": status,
        "direct": sorted(set(str(item) for item in direct)),
        "reachable": sorted(set(str(item) for item in reachable)),
        "profiles": sorted(set(str(item) for item in profiles)),
        "registry_fingerprints": sorted(
            set(str(item) for item in registry_fingerprints)
        ),
        "evidence_fingerprints": sorted(
            set(str(item) for item in evidence_fingerprints)
        ),
        "blockers": sorted(set(str(item) for item in blockers)),
        "trusted": bool(trusted),
    }
    value["fingerprint"] = _hash(value)
    return value


def _ambient_surfaces(abilities: Sequence[Any]) -> list[str]:
    surfaces: set[str] = set()
    for ability in abilities:
        surfaces.add(f"event:{ability.event}")
        surfaces.add(f"zone:{ability.active_zone}")
        for effect in ability.effects:
            operation = str(effect.get("op") or "").strip()
            if operation:
                surfaces.add(f"effect:{operation}")
        for handler in ability.handlers:
            handler_id = str(handler.get("handler_id") or "").strip()
            if handler_id:
                surfaces.add(f"handler:{handler_id}")
        schema = ability.target_schema
        if isinstance(schema, Mapping):
            for category in schema.get("categories", []):
                surfaces.add(f"target:{category}")
            for zone in schema.get("zones", []):
                surfaces.add(f"target-zone:{zone}")
    return sorted(surfaces)


def _compatibility_row(
    ability: Any,
    *,
    oracle_source_hash: str,
    rulings_source_hash: str,
) -> dict[str, Any]:
    component_ids = sorted(
        {
            str(value)
            for value in ability.provenance.get(
                "runtime_component_ids", []
            )
        }
    )
    handler_ids = sorted(
        {
            str(handler.get("handler_id"))
            for handler in ability.handlers
            if handler.get("handler_id")
        }
    )
    return {
        "ability_id": ability.ability_id,
        "semantic_key": ability.key,
        "source_oracle_hash": oracle_source_hash,
        "source_rulings_hash": rulings_source_hash,
        "handler_ids": handler_ids,
        "component_ids": component_ids,
        "tests": sorted(set(str(value) for value in ability.tests)),
        "replay_provenance": {
            "semantic_key": ability.key,
            "semantic_schema_version": ability.semantic_schema_version,
            "version": ability.version,
        },
        "removal_condition": str(
            ability.provenance.get("compatibility_removal_condition")
            or "Replace this reviewed compatibility ability with a current "
            "CardProgram whose complete runtime dependency closure is trusted."
        ),
    }


def _semantic_ability_trust_inputs(
    abilities: Sequence[Any],
) -> tuple[tuple[Any, ...], bool]:
    values = tuple(
        ability
        for ability in abilities
        if not is_structural_activated_ability_catalog_program(ability)
    )
    return values, bool(values)


def _ability_input_blockers(ability: Any, *, oracle_source_hash: str, rulings_source_hash: str) -> set[str]:
    """Validate one ability's descriptor minima and pinned source identity."""
    blockers: set[str] = set()
    if ability.capability_closure is not None:
        from .runtime_requirements import program_dynamic_runtime_requirements
        required, runtime_blockers = program_dynamic_runtime_requirements(ability)
        blockers.update(f'ability:{ability.ability_id}:{value}' for value in runtime_blockers)
        blockers.update(f'ability:{ability.ability_id}:capability:undeclared_runtime_dependency:{value}'
                        for value in required - set(ability.capability_dependencies))
    if ability.requires_arbiter:
        blockers.add(f'ability:{ability.ability_id}:requires_arbiter')
    if ability.provenance.get('source_oracle_hash') != oracle_source_hash:
        blockers.add(f'ability:{ability.ability_id}:stale_oracle_source')
    if ability.provenance.get('source_rulings_hash') != rulings_source_hash:
        blockers.add(f'ability:{ability.ability_id}:stale_rulings_source')
    return blockers


def build_program_trust_closure(
    abilities: Sequence[Any],
    residuals: Sequence[Mapping[str, Any]],
    *,
    oracle_source_hash: str,
    rulings_source_hash: str,
) -> dict[str, Any]:
    semantic_abilities, all_ignored = _semantic_ability_trust_inputs(abilities)
    direct: set[str] = set()
    reachable: set[str] = set()
    blockers: set[str] = set()
    profiles: set[str] = set()
    registries: set[str] = set()
    evidence: set[str] = set()
    legacy: list[str] = []
    capability_closed: list[str] = []
    compatibility: list[dict[str, Any]] = []
    provisional = False
    for ability in semantic_abilities:
        direct.update(ability.capability_dependencies)
        closure = ability.capability_closure
        if closure is None:
            legacy.append(ability.ability_id)
            reviewed = (
                ability.trust_level in {"trusted", "intentionally_ignored"}
                and ability.provenance.get("review_status") == "reviewed"
                and bool(ability.tests)
            )
            if reviewed:
                compatibility.append(
                    _compatibility_row(
                        ability,
                        oracle_source_hash=oracle_source_hash,
                        rulings_source_hash=rulings_source_hash,
                    )
                )
            else:
                provisional = True
        else:
            capability_closed.append(ability.ability_id)
            reachable.update(
                str(value) for value in closure.get("reachable", [])
            )
            blockers.update(
                str(value) for value in closure.get("blockers", [])
            )
            if closure.get("profile"):
                profiles.add(str(closure["profile"]))
            if closure.get("registry_fingerprint"):
                registries.add(str(closure["registry_fingerprint"]))
            if closure.get("evidence_fingerprint"):
                evidence.add(str(closure["evidence_fingerprint"]))
            if closure.get("trusted") is not True:
                blockers.add(
                    f"ability:{ability.ability_id}:capability_untrusted"
                )
        if ability.trust_level not in {"trusted", "intentionally_ignored"}:
            provisional = True
        if ability.trust_level != "intentionally_ignored":
            all_ignored = False
        blockers.update(_ability_input_blockers(ability, oracle_source_hash=oracle_source_hash,
                                                rulings_source_hash=rulings_source_hash))
    for residual in residuals:
        if residual.get("material", True):
            residual_id = str(residual.get("residual_id") or "unknown")
            face_id = str(residual.get("face_id") or "unknown")
            blockers.add(f"residual:{face_id}:{residual_id}")

    if blockers:
        trust_basis = "unresolved"
    elif provisional:
        trust_basis = "provisional"
    elif all_ignored:
        trust_basis = "non_rules_governed"
    elif capability_closed and legacy:
        trust_basis = "mixed"
    elif legacy:
        trust_basis = "legacy_reviewed"
    else:
        trust_basis = "capability_closed"
    trusted = trust_basis in {
        "capability_closed",
        "legacy_reviewed",
        "mixed",
        "non_rules_governed",
    }
    intrinsic = _layer(
        kind="intrinsic",
        status="resolved" if not blockers else "blocked",
        direct=direct,
        reachable=reachable,
        profiles=profiles,
        registry_fingerprints=registries,
        evidence_fingerprints=evidence,
        blockers=blockers,
        trusted=trusted,
    )
    if profiles:
        format_layer = _layer(
            kind="format",
            status="declared_empty",
            profiles=profiles,
            registry_fingerprints=registries,
            evidence_fingerprints=evidence,
            trusted=True,
        )
    else:
        format_layer = _layer(
            kind="format",
            status="unbound",
            blockers=("format_profile:unbound",),
            trusted=False,
        )
    result = {
        "capability_dependencies": sorted(direct),
        "capability_reachable": sorted(reachable),
        "profiles": sorted(profiles),
        "registry_fingerprints": sorted(registries),
        "evidence_fingerprints": sorted(evidence),
        "legacy_ability_ids": sorted(set(legacy)),
        "capability_closed_ability_ids": sorted(set(capability_closed)),
        "ambient_interaction_surfaces": _ambient_surfaces(abilities),
        "compatibility_provenance": sorted(
            compatibility, key=lambda row: row["ability_id"]
        ),
        "trust_basis": trust_basis,
        "strict_capability_ready": (
            trust_basis == "capability_closed" and trusted
        ),
        "closure_layers": {
            "intrinsic": intrinsic,
            "format": format_layer,
            "match": _layer(
                kind="match",
                status="unbound",
                blockers=("match_context:unbound",),
                trusted=False,
            ),
            "dynamic": _layer(
                kind="dynamic",
                status="unbound",
                blockers=("dynamic_state:unbound",),
                trusted=False,
            ),
        },
        "blockers": sorted(blockers),
        "trusted": trusted,
    }
    result["fingerprint"] = _hash(result)
    return result


def compute_match_trust_closure(
    programs: Iterable[Any],
    *,
    registry: Any,
    profile: str,
    dynamic_capabilities: Iterable[str] = (),
) -> dict[str, Any]:
    """Conservatively bind all loaded programs to one match environment."""

    values = tuple(programs)
    intrinsic = sorted(
        {
            dependency
            for program in values
            for dependency in program.capability_dependencies
        }
    )
    format_dependencies = FORMAT_PROFILE_CAPABILITIES.get(profile)
    blockers: set[str] = set()
    compatibility_blockers: set[str] = set()
    if format_dependencies is None:
        format_dependencies = ()
        blocker = f"format_profile:unknown:{profile}"
        blockers.add(blocker)
        compatibility_blockers.add(blocker)
    elif not format_dependencies:
        # The current fine-grained registry does not yet inventory the rules
        # that are always active for any complete game profile. Existing
        # reviewed play remains available, but strict capability-only creation
        # must not treat an empty declaration as proof of format closure.
        blockers.add(f"format_profile:capability_inventory_incomplete:{profile}")
    match_requested = sorted(set(intrinsic) | set(format_dependencies))
    match = registry.closure(match_requested, profile=profile)
    dynamic_requested = sorted(set(str(value) for value in dynamic_capabilities))
    dynamic = registry.closure(dynamic_requested, profile=profile)
    blockers.update(match.blockers)
    blockers.update(dynamic.blockers)
    compatibility_blockers.update(match.blockers)
    compatibility_blockers.update(dynamic.blockers)
    for program in values:
        basis = str(program.trust_closure.get("trust_basis") or "unresolved")
        if basis in {"provisional", "unresolved"}:
            blocker = f"program:{program.oracle_id}:trust_basis:{basis}"
            blockers.add(blocker)
            compatibility_blockers.add(blocker)
        elif basis in {"legacy_reviewed", "mixed"}:
            blockers.add(f"program:{program.oracle_id}:trust_basis:{basis}")
    result = {
        "profile": profile,
        "program_fingerprints": sorted(program.fingerprint for program in values),
        "intrinsic_capabilities": intrinsic,
        "format_capabilities": sorted(format_dependencies),
        "match_closure": match.to_dict(),
        "dynamic_closure": dynamic.to_dict(),
        "legacy_reviewed_programs": sorted(
            program.oracle_id
            for program in values
            if program.trust_closure.get("trust_basis")
            in {"legacy_reviewed", "mixed"}
        ),
        "blockers": sorted(blockers),
        "compatibility_blockers": sorted(compatibility_blockers),
        "strict_capability_ready": (
            not blockers
            and all(
                program.trust_closure.get("strict_capability_ready") is True
                for program in values
            )
        ),
        "compatible_ready": not compatibility_blockers,
    }
    result["fingerprint"] = _hash(result)
    return result
