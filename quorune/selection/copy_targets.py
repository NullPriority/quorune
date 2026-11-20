from __future__ import annotations

"""Fixed stack target choices over the canonical current target validator."""

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..targets import target_plan
from .target_validation import CopyTargetValidation


class CopyTargetHost(Protocol):
    def _target_snapshot(self, ref: str) -> Mapping[str, Any]: ...

    def _group_target_submission(self, plan: Any, targets: Sequence[Any]) -> dict[str, list[str]]: ...

    def _validate_semantic_targets(self, controller: str, program: Any, targets: Sequence[Any],
                                  **options: Any) -> tuple[list[str], dict[str, list[str]]]: ...

    def _target_candidate_map(self, actor: str, plan: Any, *, source_ref: str | None, acting_source: Any = None) -> Mapping[str, Sequence[str]]: ...


@dataclass(frozen=True, slots=True)
class PreparedCopyTargets:
    targets: tuple[str, ...]
    groups: FrozenMap
    snapshots: FrozenMap


def original_copy_groups(host: CopyTargetHost, schema: Mapping[str, Any], modes: Sequence[str],
                         targets: Sequence[str], groups: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    plan = target_plan(schema, modes, require_modes=bool(schema.get("modes")))
    if groups:
        if set(groups) != {group.group_id for group in plan.groups}:
            raise GameRuleError("Original copied target groups are unavailable")
        result = {group.group_id: list(groups[group.group_id]) for group in plan.groups}
    else:
        result = host._group_target_submission(plan, targets)
    if [ref for group in plan.groups for ref in result[group.group_id]] != list(targets):
        raise GameRuleError("Original copied target order changed")
    return result


def copy_target_public_schema(host: CopyTargetHost, actor: str, schema: Mapping[str, Any],
                              modes: Sequence[str], targets: Sequence[str],
                              groups: Mapping[str, Sequence[str]], source_ref: str | None, *, acting_source=None) -> dict[str, Any]:
    plan = target_plan(schema, modes, require_modes=bool(schema.get("modes")))
    original = original_copy_groups(host, schema, modes, targets, groups)
    candidates = host._target_candidate_map(actor, plan, source_ref=source_ref, acting_source=acting_source)
    return {"groups": [{**group.public_dict(candidates[group.group_id]),
                        "min": len(original[group.group_id]), "max": len(original[group.group_id])}
                       for group in plan.groups]}


def prepare_copy_targets(host: CopyTargetHost, *, actor: str, schema: Mapping[str, Any] | None,
                         modes: Sequence[str], source_ref: str | None,
                         original_targets: Sequence[str], original_groups: Mapping[str, Sequence[str]],
                         original_snapshots: Mapping[str, Any], submitted: Any, acting_source=None) -> PreparedCopyTargets:
    """Retain pinned identities; explicit grouped refs select current objects."""
    defaults = list(original_targets)
    if schema is None:
        if defaults or submitted not in (None, [], {}):
            raise GameRuleError("Copied target schema is unavailable")
        return PreparedCopyTargets((), FrozenMap(), FrozenMap())
    plan = target_plan(schema, modes, require_modes=bool(schema.get("modes")))
    original = original_copy_groups(host, schema, modes, defaults, original_groups)
    from ..announced_damage import damage_allocation_spec
    allocated = damage_allocation_spec(schema) is not None
    slots = [(group.group_id, ref) for group in plan.groups for ref in original[group.group_id]]
    values: list[tuple[str | None, Any]] = []
    if submitted is None:
        values = [(None, {"retain": index}) for index in range(len(slots))]
    elif isinstance(submitted, Mapping):
        if set(submitted) - set(original):
            raise GameRuleError("Copied target groups are malformed")
        for group in plan.groups:
            raw = submitted.get(group.group_id, ())
            if not isinstance(raw, (list, tuple)):
                raise GameRuleError("Copied target group must be an array")
            values.extend((group.group_id, value) for value in raw)
    elif isinstance(submitted, (list, tuple)):
        values = [(None, value) for value in submitted]
    else:
        raise GameRuleError("Copied target selection must be an array or group map")
    if len(values) != len(slots):
        raise GameRuleError("Copied target count must remain unchanged")
    selected: list[dict[str, str]] = []
    retained = {group.group_id: [] for group in plan.groups}
    snapshots: dict[str, Any] = {}
    changed_refs: set[str] = set()
    for position, (mapped_group, value) in enumerate(values):
        group_id = mapped_group or slots[position][0]
        keep = False
        if isinstance(value, Mapping) and set(value) == {"retain"}:
            index = value["retain"]
            if type(index) is not int or not 0 <= index < len(slots):
                raise GameRuleError("Copied target retention index is invalid")
            if allocated and index != position:
                raise GameRuleError("A retained allocated target must keep its slot")
            retained_group, ref = slots[index]
            if mapped_group is not None and mapped_group != retained_group:
                raise GameRuleError("Copied target retention changed its group")
            group_id, keep = retained_group, True
        elif isinstance(value, Mapping) and set(value) in ({"ref"}, {"group", "ref"}):
            group_id = value.get("group", group_id)
            ref = value["ref"]
        elif type(value) is str:
            ref = value
            keep = ref == slots[position][1] if allocated else ref in original.get(group_id, ())
        else:
            raise GameRuleError("Copied target selection is malformed")
        if type(group_id) is not str or group_id not in original or type(ref) is not str or not ref:
            raise GameRuleError("Copied target reference or group is invalid")
        if keep:
            snapshot = original_snapshots.get(ref)
            if not isinstance(snapshot, Mapping):
                raise GameRuleError("Original copied target identity is unavailable")
            retained[group_id].append(ref)
            snapshots[ref] = copy.deepcopy(dict(snapshot))
        else:
            changed_refs.add(ref)
        selected.append({"group": group_id, "ref": ref})
    if changed_refs.intersection(snapshots):
        raise GameRuleError("Copying two incarnations of one public target reference is unsupported")
    proof = CopyTargetValidation(
        FrozenMap({group: len(refs) for group, refs in original.items()}),
        FrozenMap(retained), FrozenMap(snapshots),
    )
    targets, groups = host._validate_semantic_targets(
        actor, None, selected, modes=modes, source_ref=source_ref,
        target_schema=schema, copy_targets=proof, acting_source=acting_source,
    )
    for ref in changed_refs:
        snapshots[ref] = dict(host._target_snapshot(ref))
    return PreparedCopyTargets(tuple(targets), FrozenMap(groups), FrozenMap(snapshots))


def _stack_target_change_subject(host, effect):
    target_ref = effect.get("_target_stack_ref") or effect.get("stack")
    item = next((current for current in host.state.stack if current.ref == target_ref), None)
    if item is None:
        return None, None, None
    program = host.semantics.get(item.semantic_key)
    return item, program, host._stack_target_schema(item, program)


def materialize_single_stack_target_change(host, effect, response):
    from ..stack_target_change_model import matches_single_announced_stack_target
    from ..target_source import stack_targeting_source
    from .target_query import target_identity_matches_snapshot

    item, program, schema = _stack_target_change_subject(host, effect)
    if item is None or schema is None or not matches_single_announced_stack_target(item):
        return {}, {}
    plan = target_plan(schema, item.modes, require_modes=bool(schema.get("modes")))
    prior_groups = item.context.get("target_groups") or {}
    prior_target, = item.targets
    if prior_target is None:
        prior_target, = (ref for refs in prior_groups.values() for ref in refs)
    original = original_copy_groups(host, schema, item.modes, (prior_target,), prior_groups)
    group, = (group for group in plan.groups if original[group.group_id])
    source_ref = host._stack_source_ref(item)
    acting_source = stack_targeting_source(host, item)
    candidates = host._target_candidate_map(
        item.controller, plan, source_ref=source_ref, acting_source=acting_source,
    )
    prior_snapshot = item.context.get("target_snapshots", {}).get(prior_target, {})
    prior_current = target_identity_matches_snapshot(host, prior_target, prior_snapshot)
    alternatives = tuple(ref for ref in candidates[group.group_id]
                         if ref != item.ref and (ref != prior_target or not prior_current))
    if not alternatives:
        return {}, {}
    key = f"{item.controller}:{item.ref}"
    public = {"groups": [{**group.public_dict(alternatives), "min": 1, "max": 1}],
              "legal_refs": list(alternatives)}
    bundle = {key: {"authoritative": dict(schema), "public": public}}
    if response is None:
        return bundle, {}

    selected = host._normalize_target_submission(response.get("targets"))
    if len(selected) != 1:
        raise GameRuleError("Choose one different legal target")
    value, = selected
    if isinstance(value, Mapping):
        if (value.get("group") or value.get("group_id")) != group.group_id:
            raise GameRuleError("The chosen target must retain its original slot")
        value = value.get("ref", value.get("target"))
    if type(value) is not str or value not in alternatives:
        raise GameRuleError("Choose one different legal target")
    targets, groups = host._validate_semantic_targets(
        item.controller, program, [{"group": group.group_id, "ref": value}],
        modes=item.modes, source_ref=source_ref, acting_source=acting_source, target_schema=schema,
    )
    return bundle, {key: {"targets": targets, "groups": groups}}


def materialize_optional_stack_target_change(host, effect, response):
    from ..target_source import stack_targeting_source

    item, _, schema = _stack_target_change_subject(host, effect)
    if item is None or schema is None or not item.targets:
        return {}, {}
    actor = item.controller
    source_ref = host._stack_source_ref(item)
    acting_source = stack_targeting_source(host, item)
    original_groups = dict(item.context.get("target_groups") or {})
    public = copy_target_public_schema(
        host, actor, schema, item.modes, item.targets, original_groups, source_ref,
        acting_source=acting_source,
    )
    for group in public["groups"]:
        group["legal_refs"] = [ref for ref in group["legal_refs"] if group["max"] and ref != item.ref]
    public["legal_refs"] = list(dict.fromkeys(ref for group in public["groups"] for ref in group["legal_refs"]))
    key = f"{actor}:{item.ref}"
    bundle = {key: {"authoritative": dict(schema), "public": public}}
    submitted = (response or {}).get("targets")
    if submitted is None:
        return bundle, {}
    assignment = prepare_copy_targets(
        host, actor=actor, schema=schema, modes=item.modes, source_ref=source_ref,
        original_targets=item.targets, original_groups=original_groups,
        original_snapshots=dict(item.context.get("target_snapshots") or {}),
        submitted=submitted, acting_source=acting_source,
    )
    if item.ref in assignment.targets:
        raise GameRuleError("A stack item cannot target itself")
    return bundle, {key: {
        "targets": list(assignment.targets), "groups": dict(assignment.groups),
        "snapshots": dict(assignment.snapshots),
    }}


def stack_copy_context(target: Any, groups: Mapping[str, Sequence[str]], snapshots: Mapping[str, Any],
                       *, permanent_spell: bool, characteristics: Mapping[str, Any]) -> dict[str, Any]:
    """Copy decisions and resolution context without rebinding kept targets."""
    return {**copy.deepcopy(dict(target.context)),
            "target_groups": {key: list(values) for key, values in groups.items()},
            "target_snapshots": copy.deepcopy(dict(snapshots)),
            "targets_revalidated": False, "targets_chosen_at_creation": True,
            "copied_from_stack": target.ref, "copy_permanent_spell": permanent_spell,
            "copy_permanent_name": str(characteristics.get("name") or target.label),
            "copy_permanent_characteristics": copy.deepcopy(dict(characteristics))}
