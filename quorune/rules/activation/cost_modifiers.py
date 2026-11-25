from __future__ import annotations

"""Read-only current-source applicability for static activation-cost modifiers."""

from dataclasses import replace

from ...ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ...activation_cost_modifiers import (ACTIVATION_GENERIC_COST_EVENT, ACTIVATION_GENERIC_COST_HANDLER,
    TARGETED_ACTIVATION_COST_HANDLER, PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER, ActivationCostModifierSource,
    ConditionalActivationReduction, OpponentControlledActivationReduction,
    AbilityLocalPublicManaAdjustment, ActivationManaReduction, FixedActivationCostModifierSpec,
    OTHER_EQUIPMENT_ACTIVATION_SUBJECT)
from ...card_program_faces import program_matches_face
from ...object_query import object_matches_query
from ...public_characteristic_aggregate_model import PublicCharacteristicAggregateSpec
from ...semantic_runtime.current_ability_components import program_has_current_ability_fragments
from ..target_cost_subjects import target_cost_subject_refs
from .ability_kinds import activation_matches_cost_scope


def _ability_local_mana_modifiers(host, seat, source, ability):
    reduction = getattr(ability, 'ability_local_mana_reduction', None)
    if reduction is None:
        return []
    from ...activation_target_values import TargetActivationReduction
    if isinstance(reduction, TargetActivationReduction):
        from .targeted_costs import ability_local_target_cost_modifiers
        return list(ability_local_target_cost_modifiers(host, seat, source, ability))
    if isinstance(reduction, OpponentControlledActivationReduction):
        count = reduction.generic if opponent_controlled_activation_threshold(host, seat, reduction) else 0
        return [FixedActivationCostModifierSpec(-count, subject_relation='any')] if count else []
    provider = source if source.controller == seat else replace(source, controller=seat)
    if isinstance(reduction, AbilityLocalPublicManaAdjustment):
        if reduction.direction == 'reduce':return []
        from ...dynamic_characteristics import query_characteristic_count
        count = query_characteristic_count(host, provider, reduction.quantity)
        return [FixedActivationCostModifierSpec(reduction.mana['GENERIC'] * count, subject_relation='any')] if count else []
    if isinstance(reduction, ConditionalActivationReduction):
        from .public_state_conditions import current_activation_public_state_matches

        count = reduction.generic if current_activation_public_state_matches(host, provider, reduction.condition) else 0
    elif isinstance(reduction, PublicCharacteristicAggregateSpec):
        from ...public_characteristic_aggregates import current_public_characteristic_aggregate

        count = max(0, current_public_characteristic_aggregate(host, seat, reduction))
    else:
        from ...dynamic_characteristics import query_characteristic_count

        count = query_characteristic_count(host, provider, reduction)
    return [FixedActivationCostModifierSpec(-count, subject_relation='any')] if count else []


def opponent_controlled_activation_threshold(host, seat, reduction):
    """Count current typed public rows separately for each active opponent."""
    if seat not in host.active_seats:
        raise ValueError('Opponent activation price requires an active actor')
    counts = {opponent: 0 for opponent in host.active_seats if opponent != seat}
    for card in host.state.cards.values():
        if card.zone != 'battlefield' or card.phased_out or card.controller not in counts:
            continue
        row = host._public_object_query_result(card, _maximum_layer=None)
        if object_matches_query(row, reduction.opponent_controlled_query):
            counts[card.controller] += 1
    return any(count >= reduction.minimum for count in counts.values())


def ability_local_mana_reductions(host, seat, source, ability):
    reduction = getattr(ability, 'ability_local_mana_reduction', None)
    if not isinstance(reduction, AbilityLocalPublicManaAdjustment) or reduction.direction != 'reduce':return ()
    from .public_state_conditions import current_activation_public_state_matches
    provider = source if source.controller == seat else replace(source, controller=seat)
    return ((ActivationManaReduction(reduction.mana),)
        if current_activation_public_state_matches(host, provider, reduction.condition) else ())


def current_activation_cost_modifiers(host, seat, source, ability):
    from ...semantic_runtime.cast_costs import default_cast_cost_component_registry

    registry = default_cast_cost_component_registry()
    result = _ability_local_mana_modifiers(host, seat, source, ability)
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
                if spec.subject_relation == OTHER_EQUIPMENT_ACTIVATION_SUBJECT and source.object_id == permanent.object_id:
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
