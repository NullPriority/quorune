from __future__ import annotations

from typing import Any, Protocol

from .card_programs.admission import program_has_complete_card_program_admission
from .morph import (
    FACE_DOWN_CAST_METHODS,
    FACE_DOWN_METHOD_RUNTIME_EVENTS,
    FixedManaMorphSpec,
    MORPH_CAST_METHOD,
)
from .semantic_runtime.morph import default_fixed_mana_morph_registry


class CompiledMorphHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def compiled_fixed_mana_face_down_method_spec(
    host: CompiledMorphHost,
    card: Any,
    *,
    method: str,
) -> FixedManaMorphSpec | None:
    """Return one current trusted fixed-cost face-down method descriptor."""

    if method not in FACE_DOWN_CAST_METHODS:
        return None
    record = host.card_record(card)
    if (
        record is None
        or record.layout != "normal"
        or record.faces
        or method not in {str(value).casefold() for value in record.keywords}
        or card.annotations.get("copy_overrides") is not None
    ):
        return None
    registry = default_fixed_mana_morph_registry()
    result: list[FixedManaMorphSpec] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id,
        active_zone="all",
        event=FACE_DOWN_METHOD_RUNTIME_EVENTS[method],
    ):
        if not host.semantic_program_is_current_trusted(program):
            continue
        if not program_has_complete_card_program_admission(program):
            continue
        for descriptor in program.handlers:
            if registry.describe(str(descriptor.get("handler_id") or "")) is None:
                continue
            result.extend(registry.lower(descriptor, None))
    return (
        result[0]
        if len(result) == 1 and result[0].method == method
        else None
    )


def compiled_fixed_mana_morph_spec(
    host: CompiledMorphHost,
    card: Any,
) -> FixedManaMorphSpec | None:
    return compiled_fixed_mana_face_down_method_spec(
        host,
        card,
        method=MORPH_CAST_METHOD,
    )


__all__ = [
    "CompiledMorphHost",
    "compiled_fixed_mana_face_down_method_spec",
    "compiled_fixed_mana_morph_spec",
]
