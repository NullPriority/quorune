from __future__ import annotations

"""Strict internal shape extraction for fixed modal programs."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..compiler.modal_templates import (
    FIXED_CHOOSE_ONE_MODAL_MECHANIC,
    FIXED_NONREPEATING_MODAL_MECHANIC,
)
from ..pawprint_modes import PAWPRINT_MECHANIC, REPEATABLE_MODE_MECHANIC
from ..targets import target_plan
from .modal_selection import (
    COMMANDER_CAST_MODE_LIMIT_FIELD, conditional_cast_mode_capability, wizard_cast_mode_limit_matches,
    DISTINCT_TURN_TRIGGER_MODES_FIELD, has_distinct_turn_trigger_modes, valid_distinct_turn_trigger_modes,
)


@dataclass(frozen=True, slots=True)
class FixedModalBranch:
    effects: tuple[Mapping[str, Any], ...]
    target_schema: Mapping[str, Any] | None
    mechanics: tuple[str, ...]


FIXED_MODAL_WRAPPER_MECHANICS = frozenset(
    {
        'fixed-spree-mode-costs',
        'fixed-tiered-mode-costs',
        'fixed-entwine-mode-costs',
        PAWPRINT_MECHANIC,
        REPEATABLE_MODE_MECHANIC,
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

    from .announced_x_modal import uses_announced_x_modal, validate_announced_x_modal_schema
    announced_x = uses_announced_x_modal(target_schema)
    if announced_x:
        try:
            validate_announced_x_modal_schema(target_schema)
        except (TypeError, ValueError, KeyError):
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
        valid, target = (True, child_schema) if announced_x else _choose_one_target_schema(child_schema)
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
    ):
        return None
    fields = {"mode_count", "min_modes", "max_modes", "modes"}
    if conditional_cast_mode_capability(target_schema):
        fields.add(COMMANDER_CAST_MODE_LIMIT_FIELD)
    if valid_distinct_turn_trigger_modes(target_schema):
        fields.add(DISTINCT_TURN_TRIGGER_MODES_FIELD)
    if set(target_schema) != fields:
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
        or not 0 <= minimum_modes <= maximum_modes <= len(definitions)
        or ((minimum_modes, maximum_modes)
            not in {(0, 1), (0, len(definitions)), (1, 1), (1, len(definitions)), (2, 2)}
            and not wizard_cast_mode_limit_matches(target_schema))
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
        if not isinstance(definition, Mapping):return None
        scoped='group_scope' in definition
        allowed={'effects','groups','mechanics'}
        if scoped:allowed|={'group_scope','globally_distinct','same_player_groups'}
        if set(definition)-allowed or not {'effects','groups','mechanics'}<=set(definition):
            return None
        raw_effects = definition.get("effects")
        raw_groups = definition.get("groups")
        raw_mechanics = definition.get("mechanics")
        if (
            not isinstance(raw_effects, (list, tuple))
            or not raw_effects
            or any(not isinstance(effect, Mapping) for effect in raw_effects)
            or not isinstance(raw_groups, (list, tuple))
            or (minimum_modes == 0 and maximum_modes > 1 and raw_groups)
            or not scoped and len(raw_groups) > 1
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
        if scoped:
            if definition['group_scope']!='mode':return None
            child={k:v for k,v in definition.items() if k not in {'effects','mechanics','group_scope'}}
            valid,target=_choose_one_target_schema(child)
            if not valid or target is None:return None
            try:qualified=target_plan({'mode_count':1,'modes':{mode_id:definition}},[mode_id])
            except (ValueError,TypeError):return None
            ids={g.group_id for g in qualified.groups}
            if target_group_ids.intersection(ids):return None
            target_group_ids.update(ids)
        elif raw_groups:
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
    wrappers = FIXED_MODAL_WRAPPER_MECHANICS
    from .combat_damage_library_access import uses_combat_damage_recipient_modes

    if minimum_modes == 0 or uses_combat_damage_recipient_modes(target_schema):
        wrappers = wrappers | {"trigger-event-normalized-damage"}
    if (
        not outer.issubset(wrappers)
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
