from __future__ import annotations

from typing import Any, Protocol

from .casting_payment_keywords import DelveSpec, ImproviseSpec
from .convoke import ConvokeSpec
from .evoke import FixedManaEvokeSpec
from .semantic_runtime.cast_costs import (
    AffinitySpec,
    CONVOKE_ACTIVE_ZONE,
    CONVOKE_COST_EVENT,
    FIXED_SPELL_COST_REDUCTION_EVENT,
    SELF_SPELL_COST_REDUCTION_HANDLER_ID,
    default_cast_cost_component_registry,
)
from .self_cast_reductions import SelfSpellCostReductionSpec


class CompiledCastCostHost(Protocol):
    semantics: Any

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def _selected_face_id(spell_program: Any) -> str:
    if spell_program is None:
        return "front"
    face_id = str(spell_program.provenance.get("face_id") or "")
    if face_id:
        return face_id
    ability_id = str(getattr(spell_program, "ability_id", "") or "")
    return ability_id.removeprefix("spell:") or "front"


def compiled_convoke_specs(
    host: CompiledCastCostHost,
    oracle_id: str,
    *,
    spell_program: Any,
) -> tuple[ConvokeSpec, ...]:
    """Return the selected face's trusted precompiled Convoke descriptor."""

    expected_face = _selected_face_id(spell_program)
    registry = default_cast_cost_component_registry()
    result: list[ConvokeSpec] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        oracle_id,
        active_zone=CONVOKE_ACTIVE_ZONE,
        event=CONVOKE_COST_EVENT,
    ):
        if not host.semantic_program_is_current_trusted(program):
            continue
        if str(program.provenance.get("face_id") or "") != expected_face:
            continue
        for descriptor in program.handlers:
            if registry.describe(str(descriptor.get("handler_id") or "")) is None:
                continue
            result.extend(
                value
                for value in registry.lower(descriptor, None)
                if isinstance(value, ConvokeSpec)
            )
    return (ConvokeSpec(),) if result else ()


def compiled_affinity_specs(
    host: CompiledCastCostHost,
    oracle_id: str,
    *,
    spell_program: Any,
) -> tuple[AffinitySpec, ...]:
    """Return the selected face's trusted precompiled Affinity descriptors."""

    expected_face = _selected_face_id(spell_program)
    registry = default_cast_cost_component_registry()
    result: list[AffinitySpec] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        oracle_id,
        active_zone=CONVOKE_ACTIVE_ZONE,
        event=CONVOKE_COST_EVENT,
    ):
        if not host.semantic_program_is_current_trusted(program):
            continue
        if str(program.provenance.get("face_id") or "") != expected_face:
            continue
        for descriptor in program.handlers:
            if registry.describe(str(descriptor.get("handler_id") or "")) is None:
                continue
            result.extend(
                value
                for value in registry.lower(descriptor, None)
                if isinstance(value, AffinitySpec)
            )
    return tuple(result)


def compiled_improvise_specs(
    host: CompiledCastCostHost,
    oracle_id: str,
    *,
    spell_program: Any,
) -> tuple[ImproviseSpec, ...]:
    """Return the selected face's trusted printed Improvise descriptor."""

    return _compiled_specs(
        host,
        oracle_id,
        spell_program=spell_program,
        spec_type=ImproviseSpec,
    )


def compiled_delve_specs(
    host: CompiledCastCostHost,
    oracle_id: str,
    *,
    spell_program: Any,
) -> tuple[DelveSpec, ...]:
    """Return the selected face's trusted printed Delve descriptor."""

    return _compiled_specs(
        host,
        oracle_id,
        spell_program=spell_program,
        spec_type=DelveSpec,
    )


def compiled_evoke_specs(
    host: CompiledCastCostHost,
    oracle_id: str,
    *,
    spell_program: Any,
) -> tuple[FixedManaEvokeSpec, ...]:
    """Return the selected face's trusted fixed-mana Evoke descriptor."""

    return _compiled_specs(
        host,
        oracle_id,
        spell_program=spell_program,
        spec_type=FixedManaEvokeSpec,
    )


def compiled_self_spell_cost_reduction_specs(
    host: CompiledCastCostHost,
    oracle_id: str,
    *,
    spell_program: Any,
) -> tuple[SelfSpellCostReductionSpec, ...]:
    """Return selected-face public self-reduction descriptors."""

    expected_face = _selected_face_id(spell_program)
    registry = default_cast_cost_component_registry()
    result: list[SelfSpellCostReductionSpec] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        oracle_id,
        active_zone="all",
        event=FIXED_SPELL_COST_REDUCTION_EVENT,
    ):
        if not host.semantic_program_is_current_trusted(program):
            continue
        if str(program.provenance.get("face_id") or "") != expected_face:
            continue
        for descriptor in program.handlers:
            if (
                descriptor.get("handler_id")
                != SELF_SPELL_COST_REDUCTION_HANDLER_ID
            ):
                continue
            value = registry.lower(descriptor, None)[0]
            if isinstance(value, SelfSpellCostReductionSpec):
                result.append(value)
    return tuple(result)


def _compiled_specs(
    host: CompiledCastCostHost,
    oracle_id: str,
    *,
    spell_program: Any,
    spec_type: type[Any],
) -> tuple[Any, ...]:
    expected_face = _selected_face_id(spell_program)
    registry = default_cast_cost_component_registry()
    result: list[Any] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        oracle_id,
        active_zone=CONVOKE_ACTIVE_ZONE,
        event=CONVOKE_COST_EVENT,
    ):
        if not host.semantic_program_is_current_trusted(program):
            continue
        if str(program.provenance.get("face_id") or "") != expected_face:
            continue
        for descriptor in program.handlers:
            if registry.describe(str(descriptor.get("handler_id") or "")) is None:
                continue
            result.extend(
                value
                for value in registry.lower(descriptor, None)
                if isinstance(value, spec_type)
            )
    return tuple(result)


__all__ = [
    "CompiledCastCostHost",
    "compiled_affinity_specs",
    "compiled_convoke_specs",
    "compiled_delve_specs",
    "compiled_evoke_specs",
    "compiled_improvise_specs",
    "compiled_self_spell_cost_reduction_specs",
]
