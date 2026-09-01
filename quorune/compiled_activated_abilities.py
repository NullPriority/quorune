from __future__ import annotations

"""Runtime access to compiler-pinned activated-ability descriptors."""

from typing import Any, Mapping, Protocol

from .abilities import ActivatedAbility
from .card_programs.admission import (
    descriptor_requires_complete_card_program,
    program_has_complete_card_program_admission,
)
from .card_programs.validation import program_source_is_current
from .replacement.immutable import thaw_value
from .semantic_runtime.activated_abilities import (
    activated_abilities_from_descriptors,
)


_EXILE_ZONE = "exile"


class CompiledActivatedAbilityHost(Protocol):
    card_db: Any
    semantics: Any

    def card_record(self, card: Any) -> Any: ...


def _face_id(record: Any, card: Any) -> str:
    if getattr(card, "active_face", None):
        return str(card.active_face)
    if getattr(record, "faces", ()):
        return str(record.faces[0].get("name") or "front")
    return "front"


def _custom_activated_abilities(
    card: Any,
) -> tuple[ActivatedAbility, ...] | None:
    characteristics = dict(
        card.annotations.get("object_characteristics")
        or card.annotations.get("token_characteristics")
        or {}
    )
    if "activated_abilities" not in characteristics:
        return None
    raw = characteristics.get("activated_abilities", ())
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(value, Mapping) for value in raw
    ):
        raise ValueError("Custom activated_abilities must be an array")
    return tuple(
        ActivatedAbility.from_dict(thaw_value(value)) for value in raw
    )


def compiled_activated_abilities(
    host: CompiledActivatedAbilityHost,
    card: Any,
) -> tuple[ActivatedAbility, ...]:
    """Return the current face's source-pinned activation catalog."""

    custom = _custom_activated_abilities(card)
    if custom is not None:
        return custom
    record = host.card_record(card)
    if record is None:
        return ()
    expected_face = _face_id(record, card)
    face_ids = tuple(
        str(face.get("name") or "front") for face in getattr(record, "faces", ())
    )
    abilities: dict[str, tuple[ActivatedAbility, bool]] = {}
    for program in sorted(
        host.semantics.programs_for_oracle(
            record.oracle_id,
            event="activate",
        ),
        key=lambda value: value.key,
    ):
        if program.provenance.get("granted_only"):
            continue
        if program.active_zone not in {
            "battlefield",
            "hand",
            "graveyard",
            _EXILE_ZONE,
            "command",
        }:
            continue
        face = str(program.provenance.get("face_id") or "").strip()
        normalized_face = (
            face_ids[0]
            if face == "front" and face_ids
            else face_ids[1]
            if face == "back" and len(face_ids) == 2
            else face
        )
        if normalized_face and normalized_face != expected_face:
            continue
        if not program_source_is_current(host.card_db, program):
            continue
        if any(
            descriptor_requires_complete_card_program(descriptor)
            for descriptor in program.handlers
        ) and not program_has_complete_card_program_admission(program):
            continue
        for ability in activated_abilities_from_descriptors(
            program.handlers
        ):
            carrier = program.ability_id.startswith("ability:catalog:")
            prior = abilities.get(ability.ability_id)
            if prior is not None and prior[0] != ability:
                if prior[1] and not carrier:
                    abilities[ability.ability_id] = (ability, False)
                    continue
                if carrier and not prior[1]:
                    continue
                raise ValueError(
                    f"Conflicting compiled ability {ability.ability_id}"
                )
            abilities[ability.ability_id] = (ability, carrier)
    return tuple(
        sorted(
            (ability for ability, _carrier in abilities.values()),
            key=lambda ability: (ability.line_index, ability.ability_id),
        )
    )


def compiled_activated_ability_dicts(
    host: CompiledActivatedAbilityHost,
    card: Any,
    *,
    error_type: type[Exception] | None = None,
) -> list[dict[str, Any]]:
    try:
        return [
            ability.to_dict()
            for ability in compiled_activated_abilities(host, card)
        ]
    except ValueError as exc:
        if error_type is None:
            raise
        raise error_type(str(exc)) from exc


__all__ = [
    "CompiledActivatedAbilityHost",
    "compiled_activated_abilities",
    "compiled_activated_ability_dicts",
]
