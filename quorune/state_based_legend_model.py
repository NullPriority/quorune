from __future__ import annotations

"""Immutable public legend choices within one CR 704.3 check."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .replacement.immutable import FrozenMap, thaw_value

LEGEND_BATCH_OPERATION_ID = 'selection.nontarget.legend-batch.v1'


class StateBasedLegendError(ValueError):
    """A pending legend-choice batch is malformed or stale."""


def legend_rule_choice_version(value: Any) -> int | None:
    if value is not None and (type(value) is not int or value != 1):
        raise StateBasedLegendError('Unsupported legend-rule choice version')
    return value


def _text(value: Any) -> bool:
    return type(value) is str and bool(value)


@dataclass(frozen=True, slots=True)
class LegendCandidateSnapshot:
    object_id: str
    logical_object_id: str
    ref: str

    def __post_init__(self) -> None:
        if not all(_text(value) for value in (self.object_id, self.logical_object_id, self.ref)):
            raise StateBasedLegendError('Legend candidate identity must be complete')

    def to_dict(self) -> dict[str, str]:
        return {'object_id': self.object_id, 'logical_object_id': self.logical_object_id, 'ref': self.ref}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LegendCandidateSnapshot:
        if not isinstance(value, Mapping) or set(value) != {'object_id', 'logical_object_id', 'ref'}:
            raise StateBasedLegendError('Legend candidate uses a closed identity schema')
        return cls(**value)


@dataclass(frozen=True, slots=True)
class LegendConflictSnapshot:
    controller: str
    name: str
    candidates: tuple[LegendCandidateSnapshot, ...]

    def __post_init__(self) -> None:
        if not _text(self.controller) or not _text(self.name):
            raise StateBasedLegendError('Legend conflict requires current controller and name')
        if (not isinstance(self.candidates, tuple) or len(self.candidates) < 2
                or any(not isinstance(value, LegendCandidateSnapshot) for value in self.candidates)):
            raise StateBasedLegendError('Legend conflict requires at least two typed candidates')
        if len({value.object_id for value in self.candidates}) != len(self.candidates):
            raise StateBasedLegendError('Legend conflict repeats a physical object')

    def to_dict(self) -> dict[str, Any]:
        return {'controller': self.controller, 'name': self.name,
                'candidates': [value.to_dict() for value in self.candidates]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LegendConflictSnapshot:
        if (not isinstance(value, Mapping) or set(value) != {'controller', 'name', 'candidates'}
                or not isinstance(value['candidates'], (list, tuple))):
            raise StateBasedLegendError('Legend conflict uses a closed schema')
        return cls(value['controller'], value['name'], tuple(
            LegendCandidateSnapshot.from_dict(candidate) for candidate in value['candidates']))


def _frame_rows(frame: FrozenMap, order: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
    from .turn_priority_model import apnap_participants

    fields = {'active_player', 'phase', 'step', 'turn_sequence', 'stack_refs',
              'battlefield', 'turn_order', 'active_seats'}
    if set(frame) != fields or any(not isinstance(frame[key], (tuple, list))
                                  for key in ('stack_refs', 'battlefield', 'turn_order', 'active_seats')):
        raise StateBasedLegendError('Legend choice frame fields are malformed')
    if (not _text(frame['phase']) or not _text(frame['step'])
            or type(frame['turn_sequence']) is not int or frame['turn_sequence'] < 0):
        raise StateBasedLegendError('Legend choice frame timing is malformed')
    if apnap_participants(frame['turn_order'], frame['active_seats'], frame['active_player']) != order:
        raise StateBasedLegendError('Legend choice order disagrees with the retained turn anchor')
    if any(not _text(ref) for ref in frame['stack_refs']):
        raise StateBasedLegendError('Legend choice stack identities must be known')
    rows = {}
    for row in frame['battlefield']:
        if not isinstance(row, Mapping) or not _text(row.get('object_id')):
            raise StateBasedLegendError('Legend choice frame has an invalid battlefield identity')
        if row['object_id'] in rows:
            raise StateBasedLegendError('Legend choice frame repeats a physical object')
        rows[row['object_id']] = row
    return rows


@dataclass(frozen=True, slots=True)
class LegendChoicePlan:
    frame: FrozenMap
    player_order: tuple[str, ...]
    groups: tuple[LegendConflictSnapshot, ...]
    kept: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.frame, FrozenMap):
            object.__setattr__(self, 'frame', FrozenMap(self.frame))
        if (not isinstance(self.player_order, tuple) or not isinstance(self.groups, tuple)
                or not self.groups or any(not isinstance(g, LegendConflictSnapshot) for g in self.groups)
                or not isinstance(self.kept, tuple)):
            raise StateBasedLegendError('Legend choice plan requires immutable groups and choices')
        rows = _frame_rows(self.frame, self.player_order)
        keys = [(group.controller, group.name) for group in self.groups]
        candidates = [(candidate, index) for index, group in enumerate(self.groups) for candidate in group.candidates]
        if len(set(keys)) != len(keys) or len({c.object_id for c, _ in candidates}) != len(candidates):
            raise StateBasedLegendError('Legend plan repeats a conflict or physical candidate')
        if len({c.ref for c, _ in candidates}) != len(candidates):
            raise StateBasedLegendError('Legend plan repeats a public reference')
        for index, group in enumerate(self.groups):
            if group.controller not in self.player_order:
                raise StateBasedLegendError('Legend conflict controller is not a live participant')
            for candidate in group.candidates:
                row = rows.get(candidate.object_id, {})
                if (row.get('logical_object_id') != candidate.logical_object_id
                        or row.get('controller') != group.controller or row.get('name') != group.name
                        or row.get('phased_out') is not False
                        or 'legendary' not in str(row.get('type_line') or '').casefold()):
                    raise StateBasedLegendError('Legend candidate does not match its current public frame')
        lookup = {candidate.object_id: index for candidate, index in candidates}
        selected = set()
        for object_id in self.kept:
            if not _text(object_id):
                raise StateBasedLegendError('Legend keeps must identify physical objects')
            index = lookup.get(object_id)
            actor = next((seat for seat in self.player_order
                          if any(i not in selected and g.controller == seat for i, g in enumerate(self.groups))), None)
            if index is None or index in selected or self.groups[index].controller != actor:
                raise StateBasedLegendError('Legend keeps violate group ownership or APNAP order')
            selected.add(index)

    @property
    def selected_groups(self) -> frozenset[int]:
        return frozenset(index for index, group in enumerate(self.groups)
                         if any(candidate.object_id in self.kept for candidate in group.candidates))

    @property
    def actor(self) -> str | None:
        return next((seat for seat in self.player_order if any(
            index not in self.selected_groups and group.controller == seat
            for index, group in enumerate(self.groups))), None)

    @property
    def complete(self) -> bool:
        return self.actor is None

    def legal_candidates(self) -> tuple[LegendCandidateSnapshot, ...]:
        return tuple(candidate for index, group in enumerate(self.groups)
                     if index not in self.selected_groups and group.controller == self.actor
                     for candidate in group.candidates)

    def choose(self, actor: str, ref: str) -> LegendChoicePlan:
        matches = [candidate for candidate in self.legal_candidates() if candidate.ref == ref]
        if actor != self.actor or len(matches) != 1:
            raise StateBasedLegendError('Keep one candidate from the current player\'s remaining legend conflicts')
        return replace(self, kept=(*self.kept, matches[0].object_id))

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'frame': thaw_value(self.frame), 'player_order': list(self.player_order),
                'groups': [group.to_dict() for group in self.groups], 'kept': list(self.kept)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LegendChoicePlan:
        if (not isinstance(value, Mapping)
                or set(value) != {'schema_version', 'frame', 'player_order', 'groups', 'kept'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or any(not isinstance(value[key], (list, tuple)) for key in ('player_order', 'groups', 'kept'))):
            raise StateBasedLegendError('Legend plan uses a closed versioned schema')
        return cls(FrozenMap(value['frame']), tuple(value['player_order']), tuple(
            LegendConflictSnapshot.from_dict(group) for group in value['groups']), tuple(value['kept']))


def completed_legend_choice_plan(value: Any, version: Any) -> LegendChoicePlan | None:
    if value is None:
        return None
    if legend_rule_choice_version(version) != 1:
        raise StateBasedLegendError('Pending legend keeps require current choice semantics')
    plan = value if isinstance(value, LegendChoicePlan) else LegendChoicePlan.from_dict(value)
    if not plan.complete:
        raise StateBasedLegendError('Only a complete keep plan may await state-based replacements')
    return plan


def legend_choices_from_frame(frame: Mapping[str, Any]) -> LegendChoicePlan | None:
    if 'legend_choices' not in frame:
        return None
    plan = completed_legend_choice_plan(frame['legend_choices'], 1)
    if plan is None or plan.frame != FrozenMap({key: value for key, value in frame.items() if key != 'legend_choices'}):
        raise StateBasedLegendError('Legend choice frame does not match the state-based batch')
    return plan
