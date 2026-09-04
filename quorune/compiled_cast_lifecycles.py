from __future__ import annotations

"""Current complete-card lookup for fixed public casting lifecycles."""

from typing import Any, Protocol

from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .card_program_faces import program_matches_face
from .card_programs.admission import program_has_complete_card_program_admission
from .cast_lifecycles import (
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
    FIXED_CAST_LIFECYCLE_RUNTIME_EVENT,
)
from .semantic_runtime.cast_lifecycles import (
    default_fixed_cast_lifecycle_registry,
)


class CompiledCastLifecycleHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def _effective_static_component_keys(
        self, card: Any
    ) -> tuple[str, ...]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def compiled_fixed_cast_lifecycle_specs(
    host: CompiledCastLifecycleHost,
    card: Any,
) -> tuple[FixedCastLifecycleSpec, ...]:
    """Return current selected-face lifecycle declarations in source order."""

    record = host.card_record(card)
    if (
        record is None
        or card.object_kind != "card"
        or card.annotations.get("copy_overrides") is not None
    ):
        return ()
    printed = {str(value).casefold() for value in record.keywords}
    registry = default_fixed_cast_lifecycle_registry()
    result: list[FixedCastLifecycleSpec] = []
    current_component_keys: frozenset[str] | None = None
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id,
        active_zone="all",
        event=FIXED_CAST_LIFECYCLE_RUNTIME_EVENT,
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
            if registry.describe(str(descriptor.get("handler_id") or "")) is None:
                continue
            for spec in registry.lower(descriptor, None):
                if spec.kind.value in printed:
                    result.append(spec)
    identities = {(spec.kind, spec.ability_id) for spec in result}
    return tuple(result) if len(identities) == len(result) else ()


def compiled_fixed_cast_lifecycle_spec(
    host: CompiledCastLifecycleHost,
    card: Any,
    kind: FixedCastLifecycleKind,
) -> FixedCastLifecycleSpec | None:
    matches = tuple(
        spec
        for spec in compiled_fixed_cast_lifecycle_specs(host, card)
        if spec.kind is kind
    )
    return matches[0] if len(matches) == 1 else None


__all__ = [
    "CompiledCastLifecycleHost",
    "compiled_fixed_cast_lifecycle_spec",
    "compiled_fixed_cast_lifecycle_specs",
]
