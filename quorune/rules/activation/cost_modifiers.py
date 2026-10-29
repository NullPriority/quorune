from __future__ import annotations

"""Read-only current-source applicability for static activation-cost modifiers."""

from ...ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ...activation_cost_modifiers import (ACTIVATION_GENERIC_COST_EVENT, ACTIVATION_GENERIC_COST_HANDLER,
    TARGETED_ACTIVATION_COST_HANDLER, PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER, ActivationCostModifierSource)
from ...card_program_faces import program_matches_face
from ...object_query import object_matches_query
from ...semantic_runtime.current_ability_components import program_has_current_ability_fragments
from ..target_cost_subjects import target_cost_subject_refs
from .ability_kinds import activation_matches_cost_scope


def current_activation_cost_modifiers(host, seat, source, ability):
    from ...semantic_runtime.cast_costs import default_cast_cost_component_registry

    registry = default_cast_cost_component_registry()
    result = []
    candidate = None
    public_objects = None
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
            for index, descriptor in enumerate(program.handlers):
                if descriptor.get('handler_id') not in {ACTIVATION_GENERIC_COST_HANDLER, TARGETED_ACTIVATION_COST_HANDLER, PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER}:
                    continue
                spec, = registry.lower(descriptor, None)
                if spec.source_is_provider and source.object_id != permanent.object_id:
                    continue
                if not activation_matches_cost_scope(host,source,ability,spec.ability_scope):
                    continue
                # Object-qualified wording follows that object's controller;
                # "abilities you activate" follows the activating player.
                subject_controller = source.controller if spec.schema_version == 3 and spec.subject_relation == 'query' else seat
                same_controller = subject_controller == permanent.controller
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
                if spec.schema_version == 3:
                    from dataclasses import replace
                    from ...self_cast_reductions import CastReductionMetric, cast_reduction_multiplier
                    multiplier = cast_reduction_multiplier(host, permanent.controller,
                        CastReductionMetric.from_dict(spec.to_dict()['multiplier']), source=permanent)
                    if not multiplier:
                        continue
                    spec = replace(spec, generic_adjustment=spec.generic_adjustment * multiplier)
                if spec.schema_version in {1, 3}:
                    result.append(spec)
                    continue
                if spec.target_condition.relation == 'query' and public_objects is None:
                    public_objects = tuple(host._public_object_query_result(card)
                        for card in host._semantic_event_sources(zones={'battlefield'}) if not card.phased_out)
                attached = host.state.cards.get(permanent.attached_to)
                attached_ref = attached.ref if attached is not None and attached.zone == 'battlefield' and not attached.phased_out else None
                refs = target_cost_subject_refs(spec.target_condition, source_ref=permanent.ref, controller=permanent.controller,
                    attached_ref=attached_ref, objects=public_objects)
                result.append(ActivationCostModifierSource(f'{permanent.logical_object_id}:{program.key}:{index}', spec, refs))
    return tuple(result)
