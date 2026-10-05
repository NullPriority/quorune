from __future__ import annotations

"""Copied target decisions over the canonical current target validator."""

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

    def _target_candidate_map(self, actor: str, plan: Any, *, source_ref: str | None) -> Mapping[str, Sequence[str]]: ...


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
                              groups: Mapping[str, Sequence[str]], source_ref: str | None) -> dict[str, Any]:
    plan = target_plan(schema, modes, require_modes=bool(schema.get("modes")))
    original = original_copy_groups(host, schema, modes, targets, groups)
    candidates = host._target_candidate_map(actor, plan, source_ref=source_ref)
    return {"groups": [{**group.public_dict(candidates[group.group_id]),
                        "min": len(original[group.group_id]), "max": len(original[group.group_id])}
                       for group in plan.groups]}


def prepare_copy_targets(host: CopyTargetHost, *, actor: str, schema: Mapping[str, Any] | None,
                         modes: Sequence[str], source_ref: str | None,
                         original_targets: Sequence[str], original_groups: Mapping[str, Sequence[str]],
                         original_snapshots: Mapping[str, Any], submitted: Any) -> PreparedCopyTargets:
    """Retain pinned identities; explicit grouped refs select current objects."""
    defaults = list(original_targets)
    if schema is None:
        if defaults or submitted not in (None, [], {}):
            raise GameRuleError("Copied target schema is unavailable")
        return PreparedCopyTargets((), FrozenMap(), FrozenMap())
    plan = target_plan(schema, modes, require_modes=bool(schema.get("modes")))
    original = original_copy_groups(host, schema, modes, defaults, original_groups)
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
            retained_group, ref = slots[index]
            if mapped_group is not None and mapped_group != retained_group:
                raise GameRuleError("Copied target retention changed its group")
            group_id, keep = retained_group, True
        elif isinstance(value, Mapping) and set(value) in ({"ref"}, {"group", "ref"}):
            group_id = value.get("group", group_id)
            ref = value["ref"]
        elif type(value) is str:
            ref = value
            keep = ref in original.get(group_id, ())
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
        target_schema=schema, copy_targets=proof,
    )
    for ref in changed_refs:
        snapshots[ref] = dict(host._target_snapshot(ref))
    return PreparedCopyTargets(tuple(targets), FrozenMap(groups), FrozenMap(snapshots))


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
