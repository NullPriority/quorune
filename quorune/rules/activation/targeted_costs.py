from __future__ import annotations

"""Target-aware activation witnesses through canonical target legality."""

from dataclasses import dataclass
from itertools import combinations

from ...activation_cost_modifiers import ActivationCostModifierSource
from ...activation_x import bind_activation_target_cardinality
from ...replacement.immutable import FrozenMap, thaw_value
from ...selection.target_validation import target_assignment_witnesses
from ...target_source import ability_targeting_source
from ...targets import available_modes, target_plan


def single_creature_target_price_plan(schema):
    if schema is None or available_modes(schema):
        raise ValueError('Target activation price requires one nonmodal creature target')
    plan = target_plan(schema)
    if len(plan.groups) != 1:
        raise ValueError('Target activation price requires exactly one target group')
    group, = plan.groups
    creature = group.creature is True or 'creature' in group.types_all or group.types_any == ('creature',)
    if (not creature or group.zones != ('battlefield',) or group.categories != ('permanent',)
            or group.min_targets != 1 or group.max_targets != 1):
        raise ValueError('Target activation price requires one required battlefield creature')
    return plan


def ability_local_target_cost_modifiers(host, seat, source, ability):
    from ...activation_target_values import TargetActivationReduction
    from ...activation_cost_modifiers import FixedActivationCostModifierSpec
    from ...object_predicate import ObjectQuerySpec
    from ...target_cost_subject import TargetCostSubjectSpec
    reduction = getattr(ability, 'ability_local_mana_reduction', None)
    if not isinstance(reduction, TargetActivationReduction):return ()
    program = host.semantics.get(host._semantic_key_for_ability(source, ability))
    schema = ability.target_schema if ability.target_schema is not None else getattr(program, 'target_schema', None)
    plan = single_creature_target_price_plan(schema)
    candidates = host._target_candidate_map(seat, plan, acting_source=ability_targeting_source(host, source), source_ref=source.ref)
    by_ref = {card.ref:card for card in host.state.cards.values() if card.zone == 'battlefield' and not card.phased_out}
    result = []
    for ref in candidates[plan.groups[0].group_id]:
        card = by_ref[ref]
        power = host._numeric_stat(card.object_id, 'power') if reduction.target_metric != 'color_count' else 0
        colors = host._public_object_query_result(card, _maximum_layer=None).colors if reduction.target_metric == 'color_count' else ()
        amount = reduction.amount(power=power, colors=colors)
        if amount:
            spec = FixedActivationCostModifierSpec(-amount, subject_relation='any', schema_version=2,
                target_condition=TargetCostSubjectSpec('query', ObjectQuerySpec(zones=('battlefield',), types_all=('creature',))))
            result.append(ActivationCostModifierSource(f'{source.logical_object_id}:local-target-price:{ref}', spec, (ref,)))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ActivationTargetCostWitness:
    modifiers: tuple
    targets: tuple[str, ...] = ()
    groups: FrozenMap = FrozenMap()
    modes: tuple[str, ...] = ()


def _selected_modifiers(modifiers, targets):
    return tuple(value.spec if isinstance(value, ActivationCostModifierSource) else value
        for value in modifiers if not isinstance(value, ActivationCostModifierSource) or value.applies(targets))


def _mode_sets(schema, response):
    selected = tuple((response or {}).get('modes') or ())
    names = available_modes(schema) if schema is not None else ()
    if selected or not names:
        return (selected,)
    minimum = int(schema.get('min_modes', schema.get('mode_count', 1)))
    maximum = int(schema.get('max_modes', schema.get('mode_count', 1)))
    return tuple(value for count in range(minimum, maximum + 1) for value in combinations(names, count))


def activation_modifier_witnesses(host, seat, source, ability, modifiers, *,
                                  targets=None, response=None, include_required=False):
    bound = tuple(value for value in modifiers if isinstance(value, ActivationCostModifierSource))
    if not bound or targets is not None:
        yield ActivationTargetCostWitness(_selected_modifiers(modifiers, targets or ()), tuple(targets or ()))
        return
    program = host.semantics.get(host._semantic_key_for_ability(source, ability))
    schema = thaw_value(ability.target_schema) if ability.target_schema is not None else getattr(program, 'target_schema', None)
    schema = bind_activation_target_cardinality(ability, schema, response or {}, hint=True,
        target_limit=len(host.state.cards) + len(host.state.players) + len(host.state.stack))
    if schema is None:
        yield ActivationTargetCostWitness(_selected_modifiers(modifiers, ()))
        return
    seen = set()
    for modes in _mode_sets(schema, response):
        plan = target_plan(schema, modes, require_modes=bool(available_modes(schema)))
        candidates = host._target_candidate_map(seat, plan,
            acting_source=ability_targeting_source(host, source), source_ref=source.ref)
        required = (None,)
        if include_required:
            required = (*required, *((group.group_id, ref) for group in plan.groups
                for ref in candidates[group.group_id] if group.max_targets))
        for needed in required:
            for selected, grouped in target_assignment_witnesses(host, plan, candidates,
                    required=needed, extra_targets=len(bound),
                    eligible_sets=tuple(frozenset(value.eligible_refs) for value in bound)):
                applied = tuple(index for index, value in enumerate(modifiers)
                    if not isinstance(value, ActivationCostModifierSource) or value.applies(selected))
                key = (modes, needed, applied)
                if key in seen:
                    continue
                seen.add(key)
                yield ActivationTargetCostWitness(_selected_modifiers(modifiers, selected), selected,
                    FrozenMap(grouped), modes)
