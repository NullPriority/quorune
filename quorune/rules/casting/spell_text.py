from __future__ import annotations

"""Assemble separately compiled spell text on one cast and stack object."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ...replacement.immutable import FrozenMap, thaw_value
from ...targets import available_modes, target_group_mappings, target_plan


CAST_TEXT_FRAMES = "cast_text_frames"
_FRAME_INDEX = "_cast_text_frame"
_TARGET_FIELDS = ("target", "card", "object", "stack", "subject", "player")


@dataclass(frozen=True, slots=True)
class SpellTextBody:
    effects: tuple[Mapping[str, Any], ...]
    target_schema: Mapping[str, Any] | None = None
    modes: tuple[str, ...] = ()
    _mode_order: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if any(not isinstance(effect, Mapping) or type(effect.get("op")) is not str
            or not effect["op"] or _FRAME_INDEX in effect for effect in self.effects):
            raise ValueError("Spell text requires closed ordinary effect instructions")
        if isinstance(self.modes, (str, bytes)) or any(type(mode) is not str or not mode for mode in self.modes):
            raise ValueError("Spell text requires a closed mode sequence")
        if self.target_schema is None and self.modes:
            raise ValueError("Spell text modes require a target definition")
        object.__setattr__(self, "effects", tuple(FrozenMap(effect) for effect in self.effects))
        object.__setattr__(self, "modes", tuple(self.modes))
        object.__setattr__(self, "_mode_order", available_modes(self.target_schema or {}))
        if self.target_schema is not None:
            object.__setattr__(self, "target_schema", FrozenMap(self.target_schema))

    def schema(self) -> dict[str, Any] | None:
        if self.target_schema is None:
            return None
        schema = thaw_value(self.target_schema)
        if self._mode_order:
            schema["modes"] = {mode: schema["modes"][mode] for mode in self._mode_order}
        return schema


def _body_target_groups(body: SpellTextBody, start: int):
    schema = body.schema()
    if schema is None:
        return None, (), {}
    if "count_parameter" in schema:
        raise ValueError("Spell text needs target cardinality bound before composition")
    plan = target_plan(schema, body.modes)
    original = target_group_mappings(schema, body.modes)
    names = {group.group_id: f"target_{start + index}" for index, group in enumerate(plan.groups)}
    local_groups = []
    shared_groups = []
    prior = []
    for raw, group in zip(original, plan.groups, strict=True):
        group_id = group.group_id
        local_groups.append({**raw, "id": group_id})
        different = tuple(dict.fromkeys((*group.different_from_groups, *(prior if plan.globally_distinct else ()))))
        if group_id in different or set(different) - names.keys():
            raise ValueError("Spell text has a target relation outside its body")
        shared_groups.append({**raw, "id": names[group_id],
            "different_from_groups": [names[other] for other in different]})
        prior.append(group_id)
    if any(left not in names or right not in names for left, right in plan.same_player_groups):
        raise ValueError("Spell text has an unbound same-player target relation")
    local = {"groups": local_groups,
        **({"globally_distinct": True} if plan.globally_distinct else {}),
        **({"same_player_groups": plan.same_player_groups} if plan.same_player_groups else {})}
    return local, tuple(shared_groups), names


def _selected_mode_frames(body: SpellTextBody, frame: FrozenMap):
    schema = body.schema()
    local_groups = frame["target_schema"]["groups"]
    cursor = 0
    for mode in body.modes:
        raw_groups = target_group_mappings(schema, (mode,), require_modes=False)
        target_schema = {"groups": raw_groups}
        parsed = target_plan(target_schema).groups
        selected = local_groups[cursor:cursor + len(parsed)]
        names = {group.group_id: frame["groups"][local["id"]]
            for group, local in zip(parsed, selected, strict=True)}
        cursor += len(parsed)
        yield {"schema_version": 1, "groups": names, "target_schema": target_schema, "modes": body.modes}, (
            schema["modes"][mode].get("effects", ())
        )


@dataclass(frozen=True, slots=True)
class CastTextComposition:
    bodies: tuple[SpellTextBody, ...]
    target_schema: FrozenMap | None
    frames: tuple[FrozenMap, ...]

    def bind(self, grouped: Mapping[str, Sequence[str]]) -> dict[str, Any]:
        """Freeze instruction order and local frames after ordinary targeting."""
        expected = {identity for frame in self.frames for identity in frame["groups"].values()}
        if set(grouped) != expected:
            raise ValueError("Declared spell text targets do not match the combined target groups")
        frames = thaw_value(self.frames)
        effects = []
        for index, (body, frame) in enumerate(zip(self.bodies, self.frames, strict=True)):
            effects.extend({**thaw_value(effect), _FRAME_INDEX: index} for effect in body.effects)
            if body.modes:
                for mode_frame, instructions in _selected_mode_frames(body, frame):
                    mode_index = len(frames)
                    frames.append(thaw_value(FrozenMap(mode_frame)))
                    effects.extend({**instruction, _FRAME_INDEX: mode_index} for instruction in instructions)
        return {"effects": effects, CAST_TEXT_FRAMES: frames}


def compose_cast_text(bodies: Sequence[SpellTextBody]) -> CastTextComposition:
    """Build one target plan without relating independent target instructions."""
    parts = tuple(bodies)
    if len(parts) < 2:
        raise ValueError("Composed spell text requires a main body and added text")
    groups = []
    relations = []
    frames = []
    for body in parts:
        local, shared, names = _body_target_groups(body, len(groups))
        groups.extend(shared)
        if local is not None:
            relations.extend((names[left], names[right]) for left, right in local.get("same_player_groups", ()))
        frames.append(FrozenMap({"schema_version": 1, "groups": names,
            "target_schema": local, "modes": body.modes}))
    schema = FrozenMap({"groups": groups, **({"same_player_groups": relations} if relations else {})}) if groups else None
    return CastTextComposition(parts, schema, tuple(frames))


def _local_target_groups(item, field, names, *, fallback=None):
    groups = item.context.get(field, fallback)
    if not isinstance(groups, Mapping):
        raise ValueError("Composed spell text lost its target groups")
    result = {}
    for key, value in names.items():
        refs = groups.get(value)
        if (not isinstance(refs, (tuple, list)) or any(
            ref is not None and (type(ref) is not str or not ref) for ref in refs
        )):
            raise ValueError("Composed spell text lost a target role")
        result[key] = list(refs)
    return result


def scoped_cast_text_instruction(item, instruction):
    """Use a read-only local view of the same physical spell for value binding."""
    if _FRAME_INDEX not in instruction:
        return item, instruction
    index = instruction[_FRAME_INDEX]
    frames = item.context.get(CAST_TEXT_FRAMES)
    if (item.kind not in {"spell", "spell_copy"} or type(index) is not int or not isinstance(frames, (list, tuple))
        or not 0 <= index < len(frames)):
        raise ValueError("Composed spell instruction requires its sealed text frame")
    frame = frames[index]
    if (not isinstance(frame, Mapping) or set(frame) != {"schema_version", "groups", "target_schema", "modes"}
        or type(frame["schema_version"]) is not int or frame["schema_version"] != 1
        or not isinstance(frame["groups"], Mapping) or not isinstance(frame["modes"], (list, tuple))
        or any(type(mode) is not str or not mode for mode in frame["modes"])):
        raise ValueError("Composed spell text frame is malformed")
    schema = frame["target_schema"]
    names = frame["groups"]
    local_order = [group.group_id for group in target_plan(schema).groups] if isinstance(schema, Mapping) else []
    if (schema is not None and not isinstance(schema, Mapping) or set(local_order) != set(names)
        or any(type(value) is not str or not value for value in names.values())
        or len(set(names.values())) != len(names)):
        raise ValueError("Composed spell text frame target identities are malformed")
    original = _local_target_groups(item, "target_groups", names, fallback={})
    current = _local_target_groups(item, "target_groups_current", names, fallback=item.context.get("target_groups", {}))
    if any(len(original[key]) != len(current[key]) for key in local_order):
        raise ValueError("Composed spell text target cardinality changed")
    if any(before is None or after not in {None, before}
        for key in local_order for before, after in zip(original[key], current[key], strict=True)):
        raise ValueError("Composed spell text replaced an announced target during resolution")
    scoped = replace(item,
        modes=list(frame["modes"]),
        targets=[ref for key in local_order for ref in current[key]],
        context={**item.context, "target_groups": original, "target_groups_current": current,
            "target_schema_override": schema})
    return scoped, {key: value for key, value in instruction.items() if key != _FRAME_INDEX}


def cast_text_instruction_has_no_target(raw: Mapping[str, Any], resolved: Mapping[str, Any]) -> bool:
    """Skip absent target-dependent results while preserving untargeted siblings."""
    if any(type(raw.get(field)) is str and raw[field].startswith(("$target.", "$target[", "$target_group."))
        and field in resolved and resolved[field] is None for field in _TARGET_FIELDS):
        return True
    if raw.get("op") != "choose_option":
        return False
    branches = raw.get("then_by_choice", {})
    resolved_branches = resolved.get("then_by_choice", {})
    if not isinstance(branches, Mapping) or not isinstance(resolved_branches, Mapping):
        return False
    nonempty = [name for name, effects in branches.items() if effects]
    return bool(nonempty) and all(
        len(branches[name]) == len(resolved_branches.get(name, ())) and all(
            cast_text_instruction_has_no_target(before, after)
            for before, after in zip(branches[name], resolved_branches[name], strict=True)
        )
        for name in nonempty
    )
