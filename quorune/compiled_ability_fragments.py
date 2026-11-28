from __future__ import annotations

from typing import Any, Protocol

from .ability_fragments import (
    AbilityFragmentError,
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    StaticComponentScopeSpec,
    StaticComponentSpec,
    StaticAbilityFragment,
    ability_fragment_to_dict,
    canonical_ability_fragments,
)
from .enchant_spec import (
    EnchantSpec,
    LinkedGraveyardCreatureEnchantSpec,
    SimpleEnchantSpec,
    TypedEnchantSpec,
)
from .card_programs.admission import program_has_complete_card_program_admission
from .off_battlefield_creatures import OFF_BATTLEFIELD_SELF_CREATURE_HANDLER
from .semantic_runtime.ability_fragments import fragments_from_descriptors


class CompiledAbilityFragmentHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def _custom_fragments(card: Any) -> tuple[StaticAbilityFragment, ...]:
    characteristics = dict(
        card.annotations.get("object_characteristics")
        or card.annotations.get("token_characteristics")
        or card.annotations.get("copy_overrides")
        or {}
    )
    raw = characteristics.get("ability_fragments", ())
    if not isinstance(raw, (list, tuple)):
        raise AbilityFragmentError(
            "Custom ability_fragments must be an array"
        )
    return canonical_ability_fragments(raw)


def _face_id(record: Any, card: Any, face_name: str | None) -> str:
    if face_name:
        return str(face_name)
    if getattr(card, "active_face", None):
        return str(card.active_face)
    if getattr(record, "faces", ()):
        return str(record.faces[0].get("name") or "front")
    return "front"


def compiled_static_ability_fragments(
    host: CompiledAbilityFragmentHost,
    card: Any,
    *,
    face_name: str | None = None,
    record: Any = None,
) -> tuple[StaticAbilityFragment, ...]:
    """Return trusted, face-pinned executable static ability fragments.

    Oracle grammar is compiled before game execution. Runtime consumers only
    lower exact handler descriptors already pinned into SemanticPrograms.
    Battlefield programs provide the current layer-6 ability snapshot, while
    all-zone programs provide characteristic-defining fragments such as
    Devoid. A fragment-owning program may observe an event other than
    ``continuous``; the fragment still describes a typed static ability.
    """

    if record is None:
        record = host.card_record(card)
    if record is None:
        return _custom_fragments(card)
    expected_face = _face_id(record, card, face_name)
    from .card_program_faces import current_shared_type_doors, room_program_face_is_active
    doors = current_shared_type_doors(card)
    fragments: list[StaticAbilityFragment] = []
    semantics = host.semantics
    programs_by_key = {}
    if getattr(semantics, "runtime_handler_compatibility_enabled", True):
        programs_by_key = {
            program.key: program
            for program in semantics.runtime_handler_programs_for_oracle(
                record.oracle_id,
                active_zone="battlefield",
                event="continuous",
            )
        }
        programs_by_key.update(
            {
                program.key: program
                for program in semantics.runtime_handler_programs_for_oracle(
                    record.oracle_id,
                    active_zone="battlefield",
                    event="characteristics.evaluate",
                )
                if program.handlers
            }
        )
    from .discard_effect_origin import PUBLIC_DISCARD_DESTINATION_SCOPE
    from .event_card_return import GRAVEYARD_SOURCE_ARRIVAL_EVENT
    from .zone_transition_model import PUBLIC_ZONES

    handler_zones = {"battlefield", "all"}
    ability_zones = set(handler_zones)
    if card.zone in {"graveyard", "stack"}:
        ability_zones.add(card.zone)
    if card.zone in PUBLIC_ZONES and not card.face_down:
        ability_zones.add(PUBLIC_DISCARD_DESTINATION_SCOPE)
    for program in semantics.programs_for_oracle(record.oracle_id):
        # Copies retain this printed ability through the arrival that precedes token cleanup.
        copiable_arrival = (card.zone in {"battlefield", "stack"} and program.active_zone == "graveyard"
                            and program.event == GRAVEYARD_SOURCE_ARRIVAL_EVENT)
        if (
            (program.active_zone in handler_zones and program.handlers)
            or ((program.active_zone in ability_zones or copiable_arrival)
                and CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage)
        ):
            programs_by_key[program.key] = program
    for program in (
        programs_by_key[key] for key in sorted(programs_by_key)
    ):
        if program.provenance.get('granted_only'):
            continue
        if not host.semantic_program_is_current_trusted(program):
            continue
        program_face = str(program.provenance.get("face_id") or "")
        if doors is not None:
            if not room_program_face_is_active(card, program):
                continue
        elif (program_face or "front") != expected_face:
            continue
        if (
            program.active_zone == "battlefield"
            and program.event == "characteristics.evaluate"
            and program.ability_id.startswith("static:")
        ):
            fragments.append(StaticComponentSpec(program.key))
        if CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage:
            fragments.append(StaticComponentSpec(program.key))
        handlers = tuple(d for d in program.handlers
                         if d.get("handler_id") != OFF_BATTLEFIELD_SELF_CREATURE_HANDLER
                         or program_has_complete_card_program_admission(program))
        descriptor_fragments = fragments_from_descriptors(handlers)
        if any(
            isinstance(fragment, StaticComponentScopeSpec)
            and fragment.parent_semantic_key != program.key
            for fragment in descriptor_fragments
        ):
            raise AbilityFragmentError(
                "Static component scope parent disagrees with its program"
            )
        fragments.extend(descriptor_fragments)
    return canonical_ability_fragments(fragments)


def compiled_enchant_spec(
    host: CompiledAbilityFragmentHost,
    card: Any,
    *,
    face_name: str | None = None,
) -> EnchantSpec | None:
    """Return the trusted compiled or resolution-created Enchant descriptor."""
    from .rules.linked_enchantment_entry import linked_enchantment_current_spec

    current = linked_enchantment_current_spec(host, card) if face_name is None else None
    if current is not None:
        return current
    fragments = compiled_static_ability_fragments(
        host,
        card,
        face_name=face_name,
    )
    record = host.card_record(card)
    if record is not None and not (face_name and face_name != record.name):
        program = host.semantics.get(f"{record.oracle_id}:spell:front")
        if program is not None and host.semantic_program_is_current_trusted(
            program
        ):
            linked_specs = tuple(
                fragment
                for fragment in fragments_from_descriptors(program.handlers)
                if isinstance(fragment, LinkedGraveyardCreatureEnchantSpec)
            )
            if len(linked_specs) == 1:
                # A reviewed linked transition owns the changing legal domain
                # after reanimation; the printed graveyard-card restriction
                # remains the ordinary cast target but cannot replace it.
                return linked_specs[0]
            if linked_specs:
                return None
    specs: tuple[EnchantSpec, ...] = tuple(
        fragment
        for fragment in fragments
        if isinstance(
            fragment,
            (
                SimpleEnchantSpec,
                TypedEnchantSpec,
                LinkedGraveyardCreatureEnchantSpec,
            ),
        )
    )
    if len(specs) == 1:
        return specs[0]
    if specs:
        return None
    return None


def compiled_ability_fragment_dicts(
    host: CompiledAbilityFragmentHost,
    card: Any,
    *,
    face_name: str | None = None,
    error_type: type[Exception] | None = None,
    record: Any = None,
) -> list[dict[str, Any]]:
    """Serialize the exact trusted fragments for characteristic evaluation."""

    try:
        return [
            ability_fragment_to_dict(fragment)
            for fragment in compiled_static_ability_fragments(
                host,
                card,
                face_name=face_name,
                record=record,
            )
        ]
    except AbilityFragmentError as exc:
        if error_type is None:
            raise
        raise error_type(str(exc)) from exc


__all__ = [
    "CompiledAbilityFragmentHost",
    "compiled_ability_fragment_dicts",
    "compiled_enchant_spec",
    "compiled_static_ability_fragments",
]
