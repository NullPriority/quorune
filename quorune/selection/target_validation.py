from __future__ import annotations

"""Shared structural target validation, including server-pinned copy choices."""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..targets import TargetPlan


def _cost_representatives(host, plan, candidates, eligible_sets, required, extra_targets):
    """Keep enough interchangeable references for all structural relations."""
    limit = sum(group.min_targets for group in plan.groups) + extra_targets + 1
    owner_matters = any(group.same_owner for group in plan.groups)
    controller_matters = bool(plan.same_player_groups)
    signatures = {}
    for ref in dict.fromkeys(ref for refs in candidates.values() for ref in refs):
        snapshot = host._target_snapshot(ref) if owner_matters or controller_matters else {}
        signatures[ref] = (tuple(ref in values for values in eligible_sets),
            tuple(ref in candidates[group.group_id] for group in plan.groups),
            snapshot.get('owner') if owner_matters else None,
            snapshot.get('controller') if controller_matters else None)
    result = {}
    for group in plan.groups:
        buckets = {}
        for ref in candidates[group.group_id]:
            buckets.setdefault(signatures[ref], []).append(ref)
        refs = [ref for bucket in buckets.values() for ref in bucket[:limit]]
        if required is not None and required[0] == group.group_id and required[1] not in refs:
            refs.append(required[1])
        result[group.group_id] = tuple(ref for ref in refs if ref in candidates[group.group_id])
    return result


def target_assignment_witnesses(host, plan, candidates, *, required=None,
                                extra_targets=0, eligible_sets=None):
    """Canonical cardinality/relation witnesses shared by target-aware prices."""
    from itertools import combinations, combinations_with_replacement, product
    if eligible_sets is not None:
        candidates = _cost_representatives(host, plan, candidates, eligible_sets, required, extra_targets)
    choices = []
    for group in plan.groups:
        refs = candidates[group.group_id]
        choose = combinations if group.distinct and not group.allow_reuse else combinations_with_replacement
        minimum = max(group.min_targets, 1 if required is not None and required[0] == group.group_id else 0)
        maximum = min(group.max_targets, len(refs) if choose is combinations else group.max_targets,
                      minimum + extra_targets)
        choices.append(tuple(value for count in range(minimum, maximum + 1) for value in choose(refs, count)
            if required is None or required[0] != group.group_id or required[1] in value))
    for values in product(*choices):
        grouped = {group.group_id: list(refs) for group, refs in zip(plan.groups, values)}
        try:
            targets = validate_grouped_target_assignment(host, plan, grouped, candidates)
        except (ValueError, GameRuleError):
            continue
        yield tuple(targets), grouped


def public_target_schema_for_witnesses(schema, witnesses):
    from copy import deepcopy
    result = deepcopy(schema)
    def filter_groups(value, selected):
        allowed = {}
        for witness in selected:
            for group, refs in witness['targets'].items():
                allowed.setdefault(group, set()).update(refs)
        for group in value.get('groups', []):
            group['legal_refs'] = [ref for ref in group['legal_refs'] if ref in allowed.get(group['id'], ())]
        if 'legal_refs' in value:
            value['legal_refs'] = [ref for ref in value['legal_refs'] if any(ref in refs for refs in allowed.values())]
    if 'mode_schemas' in result:
        for mode, value in result['mode_schemas'].items():
            filter_groups(value, [w for w in witnesses if mode in w['modes']])
        legal_modes = {mode for witness in witnesses for mode in witness['modes']}
        result['legal_modes'] = [mode for mode in result['legal_modes'] if mode in legal_modes]
        result['mode_schemas'] = {mode: value for mode, value in result['mode_schemas'].items() if mode in legal_modes}
        result['legal_refs'] = list(dict.fromkeys(ref for value in result['mode_schemas'].values()
            for group in value.get('groups', ()) for ref in group['legal_refs']))
    else:
        filter_groups(result, witnesses)
    return result


class TargetSnapshotQuery(Protocol):
    def _target_snapshot(self, ref: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CopyTargetValidation:
    """Facts captured from the original stack object, never client exemptions."""

    original_counts: FrozenMap
    retained_groups: FrozenMap
    retained_snapshots: FrozenMap

    def __post_init__(self) -> None:
        for field in ("original_counts", "retained_groups", "retained_snapshots"):
            value = getattr(self, field)
            if not isinstance(value, FrozenMap):
                object.__setattr__(self, field, FrozenMap(value))

    def fixed_plan(self, plan: TargetPlan) -> TargetPlan:
        if set(self.original_counts) != {group.group_id for group in plan.groups}:
            raise GameRuleError("Copied target groups changed")
        groups = []
        for group in plan.groups:
            count = self.original_counts[group.group_id]
            if type(count) is not int or not group.min_targets <= count <= group.max_targets:
                raise GameRuleError("Original copied target count is unavailable")
            groups.append(replace(group, min_targets=count, max_targets=count))
        return replace(plan, groups=tuple(groups))


def validate_grouped_target_assignment(
    host: TargetSnapshotQuery,
    plan: TargetPlan,
    grouped: Mapping[str, Sequence[str]],
    candidates: Mapping[str, Sequence[str]],
    *,
    copy_targets: CopyTargetValidation | None = None,
) -> list[str]:
    """Apply the same cardinality, relation, and current-legality constraints."""

    def snapshot(ref: str) -> Mapping[str, Any]:
        if copy_targets is not None and ref in copy_targets.retained_snapshots:
            return copy_targets.retained_snapshots[ref]
        return host._target_snapshot(ref)

    used_global: set[str] = set()
    for group in plan.groups:
        chosen = grouped[group.group_id]
        if not group.min_targets <= len(chosen) <= group.max_targets:
            label = "Copied target count" if copy_targets is not None else f"Target group {group.group_id}"
            raise GameRuleError(
                f"{label} requires between {group.min_targets} and {group.max_targets} target(s)"
            )
        if group.distinct and not group.allow_reuse and len(set(chosen)) != len(chosen):
            raise GameRuleError(f"Target group {group.group_id} requires distinct targets")
        legal = set(candidates[group.group_id])
        if copy_targets is not None:
            legal.update(copy_targets.retained_groups.get(group.group_id, ()))
        if any(ref not in legal for ref in chosen):
            raise GameRuleError("Selected target is not legal for this target group")
        if group.same_owner and chosen:
            owners = [snapshot(ref).get("owner") for ref in chosen]
            if any(type(owner) is not str or not owner for owner in owners) or len(set(owners)) != 1:
                raise GameRuleError("Selected targets must have the same owner")
        if any(ref in grouped.get(other, ()) for other in group.different_from_groups for ref in chosen):
            raise GameRuleError("Selected targets violate a different-target restriction")
        if plan.globally_distinct and any(ref in used_global for ref in chosen):
            raise GameRuleError("Target groups require globally distinct targets")
        used_global.update(chosen)
    for left_group, right_group in plan.same_player_groups:
        left, right = grouped.get(left_group, ()), grouped.get(right_group, ())
        if not left or not right:
            raise GameRuleError("Related target groups must both contain a target")
        if copy_targets is not None and all(
            ref in copy_targets.retained_groups.get(group, ())
            for group, refs in ((left_group, left), (right_group, right)) for ref in refs
        ):
            continue

        def controller(ref: str) -> Any:
            if copy_targets is None or ref not in copy_targets.retained_snapshots:
                return host._target_snapshot(ref).get("controller")
            original = copy_targets.retained_snapshots[ref]
            if original.get("category") == "player":
                return original.get("controller")
            current = host._target_snapshot(ref)
            identity = ("stack_id",) if "stack_id" in original else ("object_id", "zone_change_counter")
            if not all(field in original and current.get(field) == original[field] for field in identity):
                raise GameRuleError("A retained target's current controller relation is unavailable")
            return current.get("controller")

        if any(controller(left_ref) != controller(right_ref)
               for left_ref in left for right_ref in right):
            raise GameRuleError("Related targets must belong to the same player")
    return [ref for group in plan.groups for ref in grouped[group.group_id]]
