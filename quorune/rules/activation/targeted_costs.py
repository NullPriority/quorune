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
