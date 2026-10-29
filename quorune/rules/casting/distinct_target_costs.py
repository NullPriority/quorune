from __future__ import annotations

"""Count-aware witnesses for one homogeneous distinct-target group."""

from ...errors import GameRuleError
from ...selection.target_validation import validate_grouped_target_assignment
from ...target_source import spell_targeting_source
from ...targets import target_plan


def has_distinct_target_modifiers(sources):
    return any(source.spec.schema_version == 5 for source in sources)


def distinct_target_tax_assignments(host, seat, card, schema, sources, count=None):
    """Retain one legal reference witness for each count/adjustment signature."""
    plan = target_plan(schema)
    if len(plan.groups) != 1:
        return
    group, = plan.groups
    candidates = host._target_candidate_map(seat, plan,
        acting_source=spell_targeting_source(host, card), source_ref=card.ref)
    refs = candidates[group.group_id]
    minimum = group.min_targets if count is None else count
    maximum = min(group.max_targets, len(refs)) if count is None else count
    if minimum < group.min_targets or maximum > min(group.max_targets, len(refs)) or maximum < minimum:
        return
    if minimum == 0:
        empty = {group.group_id: []}
        try:
            validate_grouped_target_assignment(host, plan, empty, candidates)
        except (ValueError, GameRuleError):
            pass
        else:
            yield (), empty, 0
    signatures = {ref: tuple(source.matches_target(ref) for source in sources) for ref in refs}
    for required in refs if maximum else ():
        initial = tuple(int(flag) for flag in signatures[required])
        states = {(1, initial): (required,)}
        buckets = {}
        for ref in refs:
            if ref == required:
                continue
            if group.same_owner and host._target_snapshot(ref).get('owner') != host._target_snapshot(required).get('owner'):
                continue
            buckets.setdefault(signatures[ref], []).append(ref)
        for signature, values in buckets.items():
            updated = {}
            for (size, applied), chosen in states.items():
                for take in range(min(maximum - size, len(values)) + 1):
                    counts = tuple(value + take * flag if source.spec.schema_version == 5
                        else min(1, value + take * flag) for source, value, flag in zip(sources, applied, signature))
                    updated.setdefault((size + take, counts), (*chosen, *values[:take]))
            states = updated
        for (size, _applied), chosen in states.items():
            if not max(1, minimum) <= size <= maximum:
                continue
            grouped = {group.group_id: list(chosen)}
            try:
                targets = validate_grouped_target_assignment(host, plan, grouped, candidates)
            except (ValueError, GameRuleError):
                continue
            yield tuple(targets), grouped, size
