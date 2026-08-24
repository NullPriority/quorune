from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from ..aura import is_enchant_keyword_line, parse_enchant_line
from ..enchant_spec import TypedEnchantSpec
from ..ability_fragments import parse_protection_line
from ..death_return import PERSIST_KEYWORD, UNDYING_KEYWORD
from ..rules.capabilities import (
    CapabilityClosure,
    CapabilityRegistry,
    capability_covered_mechanics,
    capability_dependencies_for_node,
)


_PROWESS_MECHANIC = "prowess"
_BLOODTHIRST_MECHANIC = "bloodthirst"


@dataclass(frozen=True, slots=True)
class DependencyGate:
    blockers: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    closure: CapabilityClosure | None = None


def dependency_gate(
    *,
    mechanics: Iterable[str],
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    cost_schema: Mapping[str, Any] | None = None,
) -> DependencyGate:
    mechanic_ids = tuple(str(value).casefold() for value in mechanics)
    capabilities = capability_dependencies_for_node(
        effects=effects,
        target_schema=target_schema,
        mechanic_ids=mechanic_ids,
        cost_schema=cost_schema,
    )
    if capability_registry is not None and capabilities:
        closure = capability_registry.closure(
            capabilities,
            profile=capability_profile,
        )
        covered = set(capability_covered_mechanics(capabilities))
        unmapped = sorted(set(mechanic_ids) - trusted_mechanics - covered)
        return DependencyGate(
            blockers=(
                *(f"capability:{blocker}" for blocker in closure.blockers),
                *(f"mechanic:{mechanic}" for mechanic in unmapped),
            ),
            capabilities=capabilities,
            closure=closure,
        )
    return DependencyGate(
        blockers=tuple(
            f"mechanic:{mechanic}"
            for mechanic in sorted(set(mechanic_ids) - trusted_mechanics)
        )
    )


def explicit_capability_gate(
    capability: str,
    *,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate:
    return explicit_capabilities_gate(
        (capability,),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )


def explicit_capabilities_gate(
    capabilities: Iterable[str],
    *,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate:
    required = tuple(sorted(set(capabilities)))
    if not required:
        return DependencyGate(blockers=())
    if capability_registry is None:
        return DependencyGate(
            blockers=tuple(f"capability:{value}" for value in required),
            capabilities=required,
        )
    closure = capability_registry.closure(
        required, profile=capability_profile
    )
    return DependencyGate(
        blockers=tuple(
            f"capability:{blocker}" for blocker in closure.blockers
        ),
        capabilities=required,
        closure=closure,
    )


def _bloodthirst_dependency_gate(
    mechanics: tuple[str, ...],
    material_line: str,
    *,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate | None:
    if mechanics != (_BLOODTHIRST_MECHANIC,):
        return None
    if re.fullmatch(
        rf"{re.escape(_BLOODTHIRST_MECHANIC)}\s+[1-9]\d*\.?",
        material_line,
        re.IGNORECASE,
    ):
        return explicit_capability_gate(
            "counter.producer.bloodthirst",
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    return DependencyGate(
        blockers=("mechanic:bloodthirst-unsupported-wording",),
        capabilities=("counter.producer.bloodthirst",),
    )


def _ward_dependency_gate(
    mechanics: tuple[str, ...],
    material_line: str,
    *,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate | None:
    if mechanics != ("ward",):
        return None
    if re.fullmatch(
        r"Ward\s+\{\d+\}\.?",
        material_line.strip(),
        re.IGNORECASE,
    ):
        return explicit_capability_gate(
            "trigger.keyword.ward.fixed_generic",
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    return DependencyGate(
        blockers=("mechanic:ward-unsupported-cost",),
        capabilities=("trigger.keyword.ward.fixed_generic",),
    )


def _spell_cast_trigger_dependency_gate(
    mechanics: tuple[str, ...],
    material_line: str,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate | None:
    if mechanics not in {(_CASCADE_MECHANIC,), (_PROWESS_MECHANIC,)}:
        return None
    mechanic = mechanics[0]
    capability_id = f"trigger.keyword.{mechanic}"
    if material_line.strip().rstrip(".").casefold() == mechanic:
        return explicit_capability_gate(
            capability_id,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    return DependencyGate(
        blockers=(f"mechanic:{mechanic}-unsupported-wording",),
        capabilities=(capability_id,),
    )


def _enchant_dependency_gate(
    material_line: str,
    *,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate | None:
    enchant_spec = parse_enchant_line(material_line)
    if enchant_spec is not None:
        return explicit_capability_gate(
            (
                "attachment.aura.typed_restriction"
                if isinstance(enchant_spec, TypedEnchantSpec)
                else "attachment.aura.simple_object"
            ),
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    if is_enchant_keyword_line(material_line):
        # The keyword is structurally represented; its exact restriction
        # remains one source-spanned residual from the Enchant compiler.
        return DependencyGate(blockers=())
    return None


def keyword_dependency_gate(
    *,
    material_line: str,
    mechanics: tuple[str, ...],
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
) -> DependencyGate:
    """Select a reviewed keyword capability before generic mechanic gating."""

    if mechanics == ("equip",) and re.fullmatch(
        r"Equip\s+(?:\{(?:\d+|[WUBRGC])\})+\.?",
        material_line,
        re.IGNORECASE,
    ):
        return explicit_capability_gate(
            "attachment.equip.fixed_mana",
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    if mechanics == (_DREDGE_MECHANIC,) and re.fullmatch(
        r"Dredge\s+[1-9]\d*\.?",
        material_line,
        re.IGNORECASE,
    ):
        return explicit_capability_gate(
            "zone.draw.library_to_hand",
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
    if bloodthirst := _bloodthirst_dependency_gate(
        mechanics,
        material_line,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    ):
        return bloodthirst
    if mechanics == (_FABRICATE_MECHANIC,):
        matches = tuple(
            part
            for part in material_line.rstrip(".").split(",")
            if re.fullmatch(
                r"Fabricate\s+[1-9]\d*\.?",
                part.strip(),
                re.IGNORECASE,
            )
        )
        if len(matches) == 1:
            return explicit_capability_gate(
                "counter.producer.fabricate",
                capability_registry=capability_registry,
                capability_profile=capability_profile,
            )
        return DependencyGate(
            blockers=("mechanic:fabricate-unsupported-wording",),
            capabilities=("counter.producer.fabricate",),
        )
    if mechanics == (_EVOLVE_MECHANIC,):
        instances = tuple(
            part
            for part in material_line.rstrip(".").split(",")
            if part.strip().casefold() == _EVOLVE_MECHANIC
        )
        if instances:
            return explicit_capability_gate(
                "counter.producer.evolve",
                capability_registry=capability_registry,
                capability_profile=capability_profile,
            )
        return DependencyGate(
            blockers=("mechanic:evolve-unsupported-wording",),
            capabilities=("counter.producer.evolve",),
        )
    spell_cast_trigger = _spell_cast_trigger_dependency_gate(
        mechanics, material_line, capability_registry, capability_profile
    )
    if spell_cast_trigger is not None:
        return spell_cast_trigger
    if ward := _ward_dependency_gate(
        mechanics,
        material_line,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    ):
        return ward
    if mechanics in {(_PERSIST_MECHANIC,), (_UNDYING_MECHANIC,)}:
        mechanic = mechanics[0]
        if material_line.strip().rstrip(".").casefold() == mechanic:
            return explicit_capability_gate(
                f"counter.producer.{mechanic}",
                capability_registry=capability_registry,
                capability_profile=capability_profile,
            )
        return DependencyGate(
            blockers=(f"mechanic:{mechanic}-unsupported-wording",),
            capabilities=(f"counter.producer.{mechanic}",),
        )
    if mechanics == ("enchant",):
        enchant_gate = _enchant_dependency_gate(
            material_line,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        if enchant_gate is not None:
            return enchant_gate
    protection_parts = tuple(
        part.strip()
        for part in re.split(r"[,;]", material_line.rstrip("."))
        if part.strip().casefold().startswith("protection from ")
    )
    if (
        "protection" in mechanics
        and len(protection_parts) == mechanics.count("protection")
        and all(parse_protection_line(part) for part in protection_parts)
    ):
        protection_gate = explicit_capability_gate(
            "protection.typed.debt",
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        other_gate = dependency_gate(
            mechanics=(
                mechanic for mechanic in mechanics if mechanic != "protection"
            ),
            effects=(),
            target_schema=None,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        combined_gate = explicit_capabilities_gate(
            (*protection_gate.capabilities, *other_gate.capabilities),
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        return DependencyGate(
            blockers=tuple(
                dict.fromkeys(
                    (
                        *combined_gate.blockers,
                        *protection_gate.blockers,
                        *other_gate.blockers,
                    )
                )
            ),
            capabilities=combined_gate.capabilities,
            closure=combined_gate.closure,
        )
    return dependency_gate(
        mechanics=mechanics,
        effects=(),
        target_schema=None,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )


__all__ = [
    "DependencyGate",
    "dependency_gate",
    "explicit_capabilities_gate",
    "explicit_capability_gate",
    "keyword_dependency_gate",
]

_DREDGE_MECHANIC = "dredge"
_CASCADE_MECHANIC = "cascade"
_FABRICATE_MECHANIC = "fabricate"
_EVOLVE_MECHANIC = "evolve"
_PERSIST_MECHANIC = PERSIST_KEYWORD
_UNDYING_MECHANIC = UNDYING_KEYWORD
