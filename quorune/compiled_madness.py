from __future__ import annotations

"""Current complete-card lookup for ordinary fixed-mana Madness."""

from typing import Any, Protocol

from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .card_program_faces import program_matches_face
from .card_programs.admission import program_has_complete_card_program_admission
from .cast_lifecycles import FixedCastLifecycleKind, FixedCastLifecycleSpec
from .madness import (
    MADNESS_REPLACEMENT_EVENT,
    MADNESS_REPLACEMENT_HANDLER_ID,
    MADNESS_TRIGGER_EVENT,
    MADNESS_TRIGGER_HANDLER_ID,
)
from .semantic_runtime.madness import (
    MadnessDiscardReplacementHandler,
    MadnessTriggerHandler,
)


class CompiledMadnessHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def _effective_static_component_keys(
        self, card: Any
    ) -> tuple[str, ...]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def compiled_fixed_madness_spec(
    host: CompiledMadnessHost,
    card: Any,
) -> FixedCastLifecycleSpec | None:
    """Return one current Madness spec for this hand or exile incarnation."""

    record = host.card_record(card)
    if (
        record is None
        or card.object_kind != "card"
        or card.zone not in {"hand", "exile"}
        or card.annotations.get("copy_overrides") is not None
        or "madness" not in {
            str(value).casefold() for value in record.keywords
        }
    ):
        return None
    if card.zone == "hand":
        event = MADNESS_REPLACEMENT_EVENT
        program_zone = "all"
        handler_id = MADNESS_REPLACEMENT_HANDLER_ID
        handler: Any = MadnessDiscardReplacementHandler()
    else:
        event = MADNESS_TRIGGER_EVENT
        program_zone = "exile"
        handler_id = MADNESS_TRIGGER_HANDLER_ID
        handler = MadnessTriggerHandler()
    current_component_keys: frozenset[str] | None = None
    result: list[FixedCastLifecycleSpec] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id,
        active_zone=program_zone,
        event=event,
    ):
        if (
            not host.semantic_program_is_current_trusted(program)
            or not program_has_complete_card_program_admission(program)
            or not program_matches_face(record, program, card)
        ):
            continue
        if (
            card.zone == "hand"
            and CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
        ):
            if current_component_keys is None:
                current_component_keys = frozenset(
                    host._effective_static_component_keys(card)
                )
            if program.key not in current_component_keys:
                continue
        for descriptor in program.handlers:
            if descriptor.get("handler_id") != handler_id:
                continue
            result.append(handler.validate(descriptor))
    identities = {(spec.ability_id, spec.fingerprint) for spec in result}
    return result[0] if len(result) == 1 and len(identities) == 1 else None


def current_fixed_cast_lifecycle_spec(
    host: CompiledMadnessHost,
    card: Any,
    kind: FixedCastLifecycleKind,
) -> FixedCastLifecycleSpec | None:
    if kind is FixedCastLifecycleKind.MADNESS:
        return compiled_fixed_madness_spec(host, card)
    from .compiled_cast_lifecycles import compiled_fixed_cast_lifecycle_spec

    return compiled_fixed_cast_lifecycle_spec(host, card, kind)


__all__ = [
    "CompiledMadnessHost",
    "compiled_fixed_madness_spec",
    "current_fixed_cast_lifecycle_spec",
]
