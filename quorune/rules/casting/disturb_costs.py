from __future__ import annotations

"""Current front-ability authority and the correlated transformed alternative."""

from ...card_program_faces import program_matches_face
from ...card_programs.admission import program_has_complete_card_program_admission
from ...disturb_model import DISTURB_HANDLER
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry


def compiled_disturb_spec(host, card):
    record = host.card_record(card)
    if (record is None or record.layout != 'transform' or len(record.faces) != 2
            or card.object_kind != 'card' or card.annotations.get('copy_overrides') is not None):
        return None
    registry = default_cast_cost_component_registry()
    found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
            active_zone='all', event='cast.cost'):
        descriptors = [d for d in program.handlers if d.get('handler_id') == DISTURB_HANDLER]
        if not descriptors:
            continue
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):
            return None
        for descriptor in descriptors:
            for spec in registry.lower(descriptor, None):
                if (spec.front_face != str(record.faces[0]['name']) or spec.back_face != str(record.faces[1]['name'])
                        or not program_matches_face(record, program, prospective_name=spec.front_face)):
                    return None
                found.append(spec)
    return found[0] if len(found) == 1 else None


def disturb_back_cast_permission(host, seat, card, face_name=None):
    if card.owner != seat or card.zone != 'graveyard':
        return False
    spec = compiled_disturb_spec(host, card)
    return spec is not None and (face_name or card.active_face) == spec.back_face


def cast_view_zone_permission(host, seat, card):
    return host._compiled_zone_cast_permission(seat, card) or disturb_back_cast_permission(host, seat, card)


def disturb_base_options(host, seat, card, *, commander_tax):
    if not disturb_back_cast_permission(host, seat, card):
        return []
    spec = compiled_disturb_spec(host, card)
    mana = dict(spec.requirements)
    mana['GENERIC'] += commander_tax
    return [{'id': 'disturb', 'kind': 'alternate', 'label': 'Cast transformed using Disturb',
        'requirements': mana, 'disturb': spec.to_dict()}]
