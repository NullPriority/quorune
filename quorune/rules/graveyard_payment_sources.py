from __future__ import annotations

"""Shared current-source discovery for intrinsic paid graveyard permissions."""

from ..card_program_faces import program_matches_face
from ..card_programs.admission import program_has_complete_card_program_admission


def compiled_graveyard_payment_specs(host, seat, card, handler_id):
    if (card.owner != seat or card.zone != 'graveyard' or card.face_down or card.object_kind != 'card'
            or card.annotations.get('copy_overrides') is not None):
        return ()
    record = host.card_record(card)
    if record is None:
        return ()
    from ..semantic_runtime.casting_activation_metadata import default_self_zone_cast_permission_registry
    registry = default_self_zone_cast_permission_registry()
    found = []; components = None
    for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id, active_zone='all', event='cast.zone.permission'):
        descriptors = [row for row in program.handlers if row.get('handler_id') == handler_id]
        if not descriptors:
            continue
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)
                or not program_matches_face(record, program, card)):
            continue
        if components is None:
            components = frozenset(host._effective_static_component_keys(card))
        if program.key not in components:
            continue
        found.extend(spec for row in descriptors for spec in registry.lower(row, None))
    return tuple(set(found))
