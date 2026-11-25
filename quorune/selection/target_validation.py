from __future__ import annotations

"""Shared structural target validation, including server-pinned copy choices."""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..target_forms import SharedPermanentTypeScope
from ..targets import TargetGroup, TargetPlan


def _cost_representatives(host, plan, candidates, eligible_sets, required, extra_targets):
    """Keep enough interchangeable references for all structural relations."""
    limit = sum(group.min_targets for group in plan.groups) + extra_targets + 1
    owner_matters = any(group.same_owner for group in plan.groups)
    controller_matters = bool(plan.same_player_groups) or any(group.distinct_controllers for group in plan.groups)
    signatures = {}
    for ref in dict.fromkeys(ref for refs in candidates.values() for ref in refs):
        pair = plan.shared_permanent_type_pair
        snapshot = host._target_snapshot(ref) if owner_matters or controller_matters or pair is not None else {}
        signatures[ref] = (tuple(ref in values for values in eligible_sets),
            tuple(ref in candidates[group.group_id] for group in plan.groups),
            snapshot.get('owner') if owner_matters else None,
            snapshot.get('controller') if controller_matters else None,
            _permanent_type_profile(snapshot, pair.scope) if pair is not None else None)
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
    from itertools import combinations, combinations_with_replacement
    if eligible_sets is not None:
        candidates = _cost_representatives(host, plan, candidates, eligible_sets, required, extra_targets)
    choices = []
    for group in plan.groups:
        refs = candidates[group.group_id]
        choose = combinations if group.distinct and not group.allow_reuse else combinations_with_replacement
        minimum = max(group.min_targets, 1 if required is not None and required[0] == group.group_id else 0)
        maximum = min(group.max_targets, len(refs) if choose is combinations else group.max_targets,
                      minimum + extra_targets)
        required_ref = required[1] if required is not None and required[0] == group.group_id else None
        choices.append((choose, refs, range(minimum, maximum + 1), required_ref))

    def assignments(index):
        if index == len(choices):
            yield ()
            return
        choose, refs, counts, required_ref = choices[index]
        for count in counts:
            for current in choose(refs, count):
                if required_ref is not None and required_ref not in current:
                    continue
                for rest in assignments(index + 1):
                    yield (current, *rest)

    for values in assignments(0):
        grouped = {group.group_id: list(refs) for group, refs in zip(plan.groups, values)}
        try:
            targets = validate_grouped_target_assignment(host, plan, grouped, candidates)
        except (ValueError, GameRuleError):
            continue
        yield tuple(targets), grouped


def target_assignment_can_include(
    host: TargetSnapshotQuery,
    plan: TargetPlan,
    candidates: Mapping[str, Sequence[str]],
    counts: Mapping[str, int],
    eligible: frozenset[str],
) -> bool:
    """Test membership in a complete legal tuple of the announced cardinalities."""
    if set(counts) != {group.group_id for group in plan.groups} or any(
        type(counts[group.group_id]) is not int
        or not group.min_targets <= counts[group.group_id] <= group.max_targets
        for group in plan.groups
    ):
        raise ValueError("Target requirements need the chosen count for each group")
    if not eligible or not any(counts.values()):
        return False
    exact_plan = replace(plan, groups=tuple(
        replace(group, min_targets=counts[group.group_id], max_targets=counts[group.group_id])
        for group in plan.groups
    ))
    for group in exact_plan.groups:
        if group.min_targets == 0:
            continue
        refs = candidates[group.group_id]
        for ref in refs:
            if ref not in eligible:
                continue
            prioritized = {**candidates, group.group_id: (ref, *(other for other in refs if other != ref))}
            witness = next(target_assignment_witnesses(
                host, exact_plan, prioritized, required=(group.group_id, ref),
                eligible_sets=(eligible,),
            ), None)
            if witness is not None:
                return True
    return False


def target_assignment_meets_requirement(
    host: TargetSnapshotQuery,
    plan: TargetPlan,
    candidates: Mapping[str, Sequence[str]],
    grouped: Mapping[str, Sequence[str]],
    required_refs: frozenset[str],
) -> bool:
    """Apply one must-choose-if-able requirement to a legal announced tuple."""
    if not required_refs or any(required_refs.intersection(refs) for refs in grouped.values()):
        return True
    return not target_assignment_can_include(
        host, plan, candidates, {group: len(refs) for group, refs in grouped.items()}, required_refs,
    )


def single_choice_required_target_candidates(
    plan: TargetPlan, candidates: Mapping[str, Sequence[str]], required_refs: frozenset[str],
) -> Mapping[str, Sequence[str]]:
    """Filter an offer whose entire target selection contains at most one object."""
    if not required_refs or len(plan.groups) != 1:
        return candidates
    group, = plan.groups
    if group.max_targets != 1 or plan.same_player_groups or plan.shared_permanent_type_pair is not None:
        return candidates
    required = [ref for ref in candidates[group.group_id] if ref in required_refs]
    return {group.group_id: required} if required else candidates


def _offered_group_maximum(group: TargetGroup, refs: Sequence[str]) -> int:
    if not refs:
        return 0
    if group.distinct and not group.allow_reuse:
        return min(group.max_targets, len(refs))
    return group.max_targets


def _independent_requirement_counts(host, plan, candidates, required_refs):
    """Project one range per group without expanding independent count choices."""
    conditions = []
    for group in plan.groups:
        group_id = group.group_id
        refs = candidates[group_id]
        if required_refs.isdisjoint(refs):
            continue
        minimum = max(1, group.min_targets)
        maximum = _offered_group_maximum(group, refs)
        if group.same_owner or group.distinct_controllers:
            # A legal tuple that includes the required object witnesses each
            # smaller positive count by keeping that object.
            local_plan = replace(plan, groups=(group,))
            low, high = minimum, maximum
            while low <= high:
                count = (low + high) // 2
                if target_assignment_can_include(host, local_plan, {group_id: refs}, {group_id: count}, required_refs):
                    low = count + 1
                else:
                    high = count - 1
            maximum = high
        if maximum >= minimum:
            conditions.append({group_id: [minimum, maximum]})
    return conditions


def target_choice_requirement(
    host: TargetSnapshotQuery, plan: TargetPlan, candidates: Mapping[str, Sequence[str]],
    required_refs: frozenset[str],
) -> dict[str, Any] | None:
    """Project the requirement using the selected target plan and legal candidate pool."""
    if not required_refs:
        return None
    refs = required_refs.intersection(ref for row in candidates.values() for ref in row)
    if not refs:
        return None
    requirement = {"kind": "flagbearer", "legal_refs": sorted(refs)}
    variable = tuple(group for group in plan.groups if group.min_targets != group.max_targets)
    fixed_counts = {group.group_id: group.min_targets for group in plan.groups if group not in variable}
    if not variable:
        return requirement if target_assignment_can_include(host, plan, candidates, fixed_counts, refs) else None
    coupled = (plan.globally_distinct and len(plan.groups) > 1 or plan.same_player_groups
               or plan.shared_permanent_type_pair is not None
               or any(group.different_from_groups for group in plan.groups))
    if not coupled:
        conditions = _independent_requirement_counts(host, plan, candidates, refs)
    else:
        from itertools import product

        ranges = [range(group.min_targets, _offered_group_maximum(group, candidates[group.group_id]) + 1)
                  for group in variable]
        conditions = []
        for selected in product(*ranges):
            counts = dict(zip((group.group_id for group in variable), selected, strict=True))
            if target_assignment_can_include(host, plan, candidates, {**fixed_counts, **counts}, refs):
                conditions.append({group: [count, count] for group, count in counts.items()})
    return {**requirement, "when_target_counts": conditions} if conditions else None


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
        requirement = value.get("target_choice_requirement")
        if requirement is not None:
            requirement["legal_refs"] = [
                ref for ref in requirement["legal_refs"] if any(ref in refs for refs in allowed.values())
            ]
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


def target_controller_matches_snapshot(host,group,ref,snapshot):
    if not group.retain_controller:return True
    controller=snapshot.get('controller')
    return type(controller)is str and bool(controller) and host._target_snapshot(ref).get('controller')==controller


def _distinct_target_controllers(host,group,chosen,copy_targets):
    if not group.distinct_controllers:return
    retained=copy_targets.retained_groups.get(group.group_id,()) if copy_targets is not None else ()
    if copy_targets is not None and all(ref in retained for ref in chosen):return
    controllers=[]
    for ref in chosen:
        current=host._target_snapshot(ref)
        if ref in retained:
            original=copy_targets.retained_snapshots[ref]
            identity=('object_id','zone_change_counter')
            if not all(key in original and current.get(key)==original[key] for key in identity):continue
        controller=current.get('controller')
        if type(controller)is not str or not controller:raise GameRuleError('Target controller relation is unavailable')
        controllers.append(controller)
    if len(controllers)!=len(set(controllers)):
        raise GameRuleError('Selected permanents must have distinct controllers')


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


def _permanent_type_profile(snapshot, scope):
    from ..characteristic_evaluation import type_parts

    type_line = snapshot.get("type_line")
    if snapshot.get("category") != "permanent" or type(type_line) is not str:
        return frozenset()
    return scope.types.intersection(type_parts(type_line)[0])


def shared_permanent_type_pair_matches(host, pair, left, right):
    left_types = _permanent_type_profile(host._target_snapshot(left), pair.scope)
    right_types = _permanent_type_profile(host._target_snapshot(right), pair.scope)
    return bool(left_types & right_types)


def shared_type_pair_candidates(host, pair, candidates):
    """Expose only references with a distinct, type-compatible partner."""
    left, right = candidates[pair.left], candidates[pair.right]
    profiles = {ref: _permanent_type_profile(host._target_snapshot(ref), pair.scope)
                for ref in dict.fromkeys((*left, *right))}
    matched_left, matched_right = set(), set()
    for left_ref in left:
        for right_ref in right:
            if left_ref != right_ref and profiles[left_ref] & profiles[right_ref]:
                matched_left.add(left_ref)
                matched_right.add(right_ref)
    return {
        pair.left: [ref for ref in left if ref in matched_left],
        pair.right: [ref for ref in right if ref in matched_right],
    }


def shared_type_pair_resolution_rejections(host, pair, grouped):
    if pair is None:
        return ()
    left = next(iter(grouped.get(pair.left, ())), None)
    right = next(iter(grouped.get(pair.right, ())), None)
    if right is None:
        return ()
    if pair.scope is SharedPermanentTypeScope.ARTIFACT_OR_CREATURE:
        if left is None or not shared_permanent_type_pair_matches(host, pair, left, right):
            return ((pair.right, right),)
    elif left is not None and not shared_permanent_type_pair_matches(host, pair, left, right):
        return ((pair.left, left), (pair.right, right))
    return ()


def _validate_shared_type_pair_assignment(host, pair, grouped, copy_targets):
    if pair is None:
        return
    left, = grouped[pair.left]
    right, = grouped[pair.right]
    selected = ((pair.left, left), (pair.right, right))
    if copy_targets is not None:
        from .target_query import target_identity_matches_snapshot

        retained = tuple((group, ref) for group, ref in selected
                         if ref in copy_targets.retained_groups.get(group, ()))
        if len(retained) == len(selected):
            return
        for _group, ref in retained:
            if not target_identity_matches_snapshot(host, ref, copy_targets.retained_snapshots[ref]):
                raise GameRuleError("A retained permanent's current shared type is unavailable")
    if not shared_permanent_type_pair_matches(host, pair, left, right):
        raise GameRuleError("Selected permanents must share a required card type")


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
        _distinct_target_controllers(host,group,chosen,copy_targets)
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
    _validate_shared_type_pair_assignment(host, plan.shared_permanent_type_pair, grouped, copy_targets)
    return [ref for group in plan.groups for ref in grouped[group.group_id]]
