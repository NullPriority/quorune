from __future__ import annotations

from typing import Any, Protocol

from .card_programs.admission import program_has_complete_card_program_admission
from .kicker import FixedManaKickerSpec, KICKER_MECHANIC_ID, KICKER_RUNTIME_EVENT
from .nonmana_kicker import FixedNonmanaKickerSpec
from .semantic_runtime.kicker import default_fixed_mana_kicker_registry


class CompiledKickerHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def compiled_kicker_spec(
    host: CompiledKickerHost,
    card: Any,
) -> FixedManaKickerSpec | FixedNonmanaKickerSpec | None:
    """Return one complete-card-admitted source-pinned Kicker descriptor."""

    record = host.card_record(card)
    if (
        record is None
        or record.layout != "normal"
        or record.faces
        or KICKER_MECHANIC_ID
        not in {str(value).casefold() for value in record.keywords}
        or card.annotations.get("copy_overrides") is not None
    ):
        return None
    registry = default_fixed_mana_kicker_registry()
    result: list[FixedManaKickerSpec | FixedNonmanaKickerSpec] = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id,
        active_zone="all",
        event=KICKER_RUNTIME_EVENT,
    ):
        if (
            not host.semantic_program_is_current_trusted(program)
            or not program_has_complete_card_program_admission(program)
        ):
            continue
        for descriptor in program.handlers:
            if registry.describe(str(descriptor.get("handler_id") or "")) is None:
                continue
            result.extend(registry.lower(descriptor, None))
    return result[0] if len(result) == 1 else None


def compiled_fixed_mana_kicker_spec(host,card):
    """Compatibility entry point retaining the historical fixed-mana return type."""
    spec=compiled_kicker_spec(host,card)
    return spec if isinstance(spec,FixedManaKickerSpec) else None


def validate_kicker_commit(host,card,option):
    if str(option.get('kind') or '').casefold()!='kicker':return
    spec=compiled_kicker_spec(host,card)
    if spec is None or option.get('kicker_fingerprint')!=spec.fingerprint:
        raise ValueError('The sealed Kicker contract changed before commit')
    if isinstance(spec,FixedNonmanaKickerSpec):
        from .replacement.immutable import thaw_value
        expected=[thaw_value(v) for v in spec.additional_costs]
        current=option.get('additional_costs')
        if not isinstance(current,list) or current[-len(expected):]!=expected:
            raise ValueError('The sealed nonmana Kicker payment changed before commit')


__all__ = ["CompiledKickerHost", "compiled_fixed_mana_kicker_spec"]
