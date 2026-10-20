from __future__ import annotations

"""Read the Defender-only exception after the characteristic query finishes."""

from ..card_program_faces import program_matches_face
from ..defender_permission import DEFENDER_DECLARATION_EVENT, DEFENDER_DECLARATION_HANDLER
from ..semantic_runtime.current_ability_components import program_has_current_ability_fragments


def current_defender_declaration_permission(host, card, characteristics):
    if card.zone != 'battlefield' or card.phased_out or card.controller not in host.active_seats:
        return False
    record = host.card_record(card)
    if record is None:
        return False
    from ..semantic_runtime.continuous_components import default_continuous_effect_component_registry
    registry = default_continuous_effect_component_registry()
    programs = host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
        active_zone='battlefield', event=DEFENDER_DECLARATION_EVENT)
    for program in programs:
        if (not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record, program, card)
                or not program_has_current_ability_fragments(program, characteristics)):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') != DEFENDER_DECLARATION_HANDLER:
                continue
            rule, = registry.lower(descriptor, None)
            if host._fixed_public_state_condition_holds(card, rule.source_condition, require_available=True):
                return True
    return False
