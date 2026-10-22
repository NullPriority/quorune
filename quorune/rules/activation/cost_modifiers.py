from __future__ import annotations

"""Read-only current-source applicability for static activation-cost modifiers."""

from ...ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ...activation_cost_modifiers import ACTIVATION_GENERIC_COST_EVENT, ACTIVATION_GENERIC_COST_HANDLER
from ...card_program_faces import program_matches_face
from ...object_query import object_matches_query
from ...semantic_runtime.current_ability_components import program_has_current_ability_fragments
from .ability_kinds import activation_matches_cost_scope


def current_activation_cost_modifiers(host, seat, source, ability):
    from ...semantic_runtime.cast_costs import default_cast_cost_component_registry

    registry = default_cast_cost_component_registry()
    result = []
    candidate = None
    for permanent in host._semantic_event_sources(zones={'battlefield'}):
        if permanent.zone != 'battlefield' or permanent.phased_out or permanent.controller not in host.active_seats:
            continue
        record = host.card_record(permanent)
        if record is None:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
            active_zone='battlefield', event=ACTIVATION_GENERIC_COST_EVENT)
        for program in programs:
            if (not host.semantic_program_is_current_trusted(program)
                    or not program_matches_face(record, program, permanent)
                    or (CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage
                        and not program_has_current_ability_fragments(program, host._effective_card_data(permanent)))):
                continue
            for descriptor in program.handlers:
                if descriptor.get('handler_id') != ACTIVATION_GENERIC_COST_HANDLER:
                    continue
                spec, = registry.lower(descriptor, None)
                if not activation_matches_cost_scope(host,source,ability,spec.ability_scope):
                    continue
                same_controller = seat == permanent.controller
                if (spec.controller_relation == 'source_controller' and not same_controller
                        or spec.controller_relation == 'source_opponents' and same_controller):
                    continue
                if spec.subject_relation != 'any':
                    if source.zone != 'battlefield':
                        continue
                    if spec.subject_relation == 'enchanted_object' and permanent.attached_to != source.object_id:
                        continue
                    if candidate is None:
                        candidate = host._public_object_query_result(source)
                    if not object_matches_query(candidate, spec.predicate):
                        continue
                result.append(spec)
    return tuple(result)
