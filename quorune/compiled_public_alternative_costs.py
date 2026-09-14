from __future__ import annotations

"""Current complete-card lookup for fixed public alternative costs."""

from typing import Any, Protocol

from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .card_program_faces import program_matches_face
from .card_programs.admission import program_has_complete_card_program_admission
from .public_alternative_costs import (
    FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
    FixedPublicAlternativeCostSpec,
)
from .semantic_runtime.public_alternative_costs import (
    default_fixed_public_alternative_cost_registry,
)


class CompiledPublicAlternativeCostHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def _effective_static_component_keys(
        self, card: Any
    ) -> tuple[str, ...]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def compiled_fixed_public_alternative_cost_specs(
    host: CompiledPublicAlternativeCostHost,
    card: Any,
) -> tuple[FixedPublicAlternativeCostSpec, ...]:
    """Return current selected-face declarations in source order."""

    record = host.card_record(card)
    if (
        record is None
        or card.object_kind != "card"
        or card.annotations.get("copy_overrides") is not None
    ):
        return ()
    registry = default_fixed_public_alternative_cost_registry()
    result: list[FixedPublicAlternativeCostSpec] = []
    current_component_keys: frozenset[str] | None = None
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id,
        active_zone="all",
        event=FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
    ):
        if (
            not host.semantic_program_is_current_trusted(program)
            or not program_has_complete_card_program_admission(program)
            or not program_matches_face(record, program, card)
        ):
            continue
        if CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage:
            if current_component_keys is None:
                current_component_keys = frozenset(
                    host._effective_static_component_keys(card)
                )
            if program.key not in current_component_keys:
                continue
        for descriptor in program.handlers:
            if registry.describe(
                str(descriptor.get("handler_id") or "")
            ) is None:
                continue
            result.extend(registry.lower(descriptor, None))
    identities = {spec.ability_id for spec in result}
    return tuple(result) if len(identities) == len(result) else ()


__all__ = [
    "CompiledPublicAlternativeCostHost",
    "compiled_fixed_public_alternative_cost_specs",
]
