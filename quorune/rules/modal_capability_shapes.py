from __future__ import annotations

"""Strict internal shape extraction for fixed modal programs."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..compiler.modal_templates import (
    FIXED_CHOOSE_ONE_MODAL_MECHANIC,
    FIXED_NONREPEATING_MODAL_MECHANIC,
)
from ..targets import target_plan


@dataclass(frozen=True, slots=True)
class FixedModalBranch:
    effects: tuple[Mapping[str, Any], ...]
    target_schema: Mapping[str, Any] | None
    mechanics: tuple[str, ...]


FIXED_MODAL_WRAPPER_MECHANICS = frozenset(
    {
        'fixed-spree-mode-costs',
        'fixed-entwine-mode-costs',
        "cr-603-handling-triggered-abilities",
        "exhaust",
        "fixed-typed-event-effect-trigger",
        "trigger-event-normalized-card-draw",
        "trigger-event-normalized-life-gain",
        "trigger-event-normalized-self-attack",
        "trigger-event-normalized-spell-cast",
        "trigger-event-normalized-zone-change",
        "trigger-event-normalized-public-action",
        "trigger-event-spell-cast-fixed-characteristics",
    }
)


def _choose_one_target_schema(schema):
    if schema == {'groups': []}:
        return True, None
    if 'modes' in schema:
        return False, None
    grouped = 'groups' in schema
    if grouped:
        groups = schema['groups']
        if (set(schema) - {'groups','globally_distinct','same_player_groups'}
                or not isinstance(groups,(list,tuple)) or not groups
                or any(not isinstance(g,Mapping) or 'groups' in g or 'modes' in g for g in groups)
                or 'globally_distinct' in schema and type(schema['globally_distinct']) is not bool):
            return False, None
    try:
        plan = target_plan(schema)
    except (TypeError,ValueError):
        return False, None
    if not plan.groups or not grouped and len(plan.groups) != 1:
        return False, None
    if grouped:
        ids = {g.group_id for g in plan.groups}
        pairs = schema.get('same_player_groups',())
        if (not isinstance(pairs,(list,tuple)) or any(not isinstance(p,(list,tuple)) or len(p)!=2
                or any(type(v) is not str or v not in ids for v in p) for p in pairs)
                or any(set(g.different_from_groups)-ids for g in plan.groups)):
            return False, None
    return True, schema


def fixed_choose_one_modal_branches(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[FixedModalBranch, ...] | None:
    """Return exact branches, or ``None`` for every malformed modal shape."""

    mechanics = tuple(str(value).casefold() for value in mechanic_ids)
    if (
        effects
        or len(mechanics) != len(set(mechanics))
        or FIXED_CHOOSE_ONE_MODAL_MECHANIC not in mechanics
        or not isinstance(target_schema, Mapping)
        or set(target_schema) != {"mode_count", "modes"}
        or type(target_schema.get("mode_count")) is not int
        or target_schema.get("mode_count") != 1
    ):
        return None
    definitions = target_schema.get("modes")
    if not isinstance(definitions, Mapping) or len(definitions) not in {2, 3}:
        return None
    expected_ids = tuple(
        f"mode_{index}" for index in range(1, len(definitions) + 1)
    )
    if tuple(definitions) != expected_ids:
        return None

    branches: list[FixedModalBranch] = []
    represented = {FIXED_CHOOSE_ONE_MODAL_MECHANIC}
    for definition in definitions.values():
        if not isinstance(definition, Mapping):
            return None
        raw_effects = definition.get("effects")
        raw_mechanics = definition.get("mechanics")
        if (
            not isinstance(raw_effects, (list, tuple))
            or not raw_effects
            or any(not isinstance(effect, Mapping) for effect in raw_effects)
            or not isinstance(raw_mechanics, (list, tuple))
            or not raw_mechanics
            or any(
                not isinstance(mechanic, str)
                or not mechanic
                or mechanic != mechanic.casefold()
                for mechanic in raw_mechanics
            )
            or len(raw_mechanics) != len(set(raw_mechanics))
            or FIXED_CHOOSE_ONE_MODAL_MECHANIC in raw_mechanics
        ):
            return None
        child_schema = {
            key: value
            for key, value in definition.items()
            if key not in {"effects", "mechanics"}
        }
        valid, target = _choose_one_target_schema(child_schema)
        if not valid:
            return None
        branch_mechanics = tuple(raw_mechanics)
        represented.update(branch_mechanics)
        branches.append(
            FixedModalBranch(
                effects=tuple(dict(effect) for effect in raw_effects),
                target_schema=target,
                mechanics=branch_mechanics,
            )
        )
    if set(mechanics) != represented:
        return None
    return tuple(branches)


def fixed_nonrepeating_modal_branches(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[FixedModalBranch, ...] | None:
    """Return exact bounded nonrepeating branches, or fail closed."""

    mechanics = tuple(str(value).casefold() for value in mechanic_ids)
    if (
        effects
        or len(mechanics) != len(set(mechanics))
        or FIXED_NONREPEATING_MODAL_MECHANIC not in mechanics
        or FIXED_CHOOSE_ONE_MODAL_MECHANIC in mechanics
        or not isinstance(target_schema, Mapping)
        or set(target_schema)
        != {"mode_count", "min_modes", "max_modes", "modes"}
    ):
        return None
    mode_count = target_schema.get("mode_count")
    minimum_modes = target_schema.get("min_modes")
    maximum_modes = target_schema.get("max_modes")
    definitions = target_schema.get("modes")
    if (
        type(mode_count) is not int
        or type(minimum_modes) is not int
        or type(maximum_modes) is not int
        or mode_count != minimum_modes
        or not isinstance(definitions, Mapping)
        or len(definitions) not in {2, 3, 4, 5}
        or not 1 <= minimum_modes <= maximum_modes <= len(definitions)
        or (minimum_modes, maximum_modes)
        not in {(1, 1), (1, len(definitions)), (2, 2)}
    ):
        return None
    expected_ids = tuple(
        f"mode_{index}" for index in range(1, len(definitions) + 1)
    )
    if tuple(definitions) != expected_ids:
        return None

    branches: list[FixedModalBranch] = []
    represented = {FIXED_NONREPEATING_MODAL_MECHANIC}
    target_group_ids: set[str] = set()
    for mode_id, definition in definitions.items():
        if not isinstance(definition, Mapping) or set(definition) != {
            "effects",
            "groups",
            "mechanics",
        }:
            return None
        raw_effects = definition.get("effects")
        raw_groups = definition.get("groups")
        raw_mechanics = definition.get("mechanics")
        if (
            not isinstance(raw_effects, (list, tuple))
            or not raw_effects
            or any(not isinstance(effect, Mapping) for effect in raw_effects)
            or not isinstance(raw_groups, (list, tuple))
            or len(raw_groups) > 1
            or any(not isinstance(group, Mapping) for group in raw_groups)
            or not isinstance(raw_mechanics, (list, tuple))
            or not raw_mechanics
            or any(
                not isinstance(mechanic, str)
                or not mechanic
                or mechanic != mechanic.casefold()
                for mechanic in raw_mechanics
            )
            or len(raw_mechanics) != len(set(raw_mechanics))
            or FIXED_NONREPEATING_MODAL_MECHANIC in raw_mechanics
        ):
            return None
        target = None
        if raw_groups:
            group = dict(raw_groups[0])
            expected_group_id = f"{mode_id}_target_1"
            if group.get("id") != expected_group_id:
                return None
            if expected_group_id in target_group_ids:
                return None
            try:
                plan = target_plan(group)
            except (TypeError, ValueError):
                return None
            if len(plan.groups) != 1:
                return None
            target_group_ids.add(expected_group_id)
            target = {
                key: value for key, value in group.items() if key != "id"
            }
        branch_mechanics = tuple(raw_mechanics)
        represented.update(branch_mechanics)
        branches.append(
            FixedModalBranch(
                effects=tuple(dict(effect) for effect in raw_effects),
                target_schema=target,
                mechanics=branch_mechanics,
            )
        )
    outer = set(mechanics) - represented
    if (
        not outer.issubset(FIXED_MODAL_WRAPPER_MECHANICS)
        or set(mechanics) != represented | outer
    ):
        return None
    return tuple(branches)


__all__ = [
    "FIXED_MODAL_WRAPPER_MECHANICS",
    "FixedModalBranch",
    "fixed_choose_one_modal_branches",
    "fixed_nonrepeating_modal_branches",
]
