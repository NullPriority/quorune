from __future__ import annotations

from typing import Any, Protocol

from .card_program_faces import program_matches_face
from .card_programs.admission import program_has_complete_card_program_admission
from .flashback import (
    FixedManaFlashbackSpec,
    FLASHBACK_MECHANIC_ID,
    FLASHBACK_RUNTIME_EVENT,
)
from .semantic_runtime.flashback import default_fixed_mana_flashback_registry
from .semantic_runtime.casting_activation_metadata import (
    compiled_self_zone_cast_permission,
)


class CompiledFlashbackHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _temporary_play_permission(self, seat: str, card: Any) -> Any: ...


def compiled_ordinary_zone_cast_permission(
    host: CompiledFlashbackHost,
    seat: str,
    card: Any,
    *, include_source_grants: bool = True,
) -> bool:
    """Return whether current typed authority permits the printed cost."""

    permission = host._temporary_play_permission(seat, card)
    if permission is not None and bool(permission.get("allow_spell", True)):
        return True
    if card.owner != seat:
        return False
    if card.zone in set(card.annotations.get("cast_from") or []):
        return True
    if compiled_self_zone_cast_permission(host, seat, card):
        return True
    from .rules.casting.turn_cast_grants import normal_turn_grant_permission
    return include_source_grants and normal_turn_grant_permission(host, seat, card)


def compiled_fixed_mana_flashback_spec(
    host: CompiledFlashbackHost,
    card: Any,
) -> FixedManaFlashbackSpec | None:
    """Return one current, complete-card-admitted printed Flashback spec."""

    record = host.card_record(card)
    if (
        record is None
        or FLASHBACK_MECHANIC_ID
        not in {str(value).casefold() for value in record.keywords}
        or card.object_kind != "card"
        or card.annotations.get("copy_overrides") is not None
    ):
        return None
    registry = default_fixed_mana_flashback_registry()
    result: list[FixedManaFlashbackSpec] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id,
        active_zone="all",
        event=FLASHBACK_RUNTIME_EVENT,
    ):
        if (
            not host.semantic_program_is_current_trusted(program)
            or not program_has_complete_card_program_admission(program)
            or not program_matches_face(record, program, card)
        ):
            continue
        for descriptor in program.handlers:
            if registry.describe(str(descriptor.get("handler_id") or "")) is not None:
                result.extend(registry.lower(descriptor, None))
    return result[0] if len(result) == 1 else None


__all__ = [
    "CompiledFlashbackHost",
    "compiled_fixed_mana_flashback_spec",
    "compiled_ordinary_zone_cast_permission",
]
