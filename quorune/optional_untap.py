from __future__ import annotations

"""Retained controller choice within the existing physical untap step."""

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping

from .errors import GameRuleError
from .model import GameState, StackItem
from .replacement.immutable import FrozenMap, thaw_value
from .untap_step import UntapStepPlan
from .tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION


@dataclass(frozen=True, slots=True)
class OptionalUntapSubject:
    object_id: str
    logical_object_id: str
    ref: str
    controller: str
    tapped: bool

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value for value in
                (self.object_id, self.logical_object_id, self.ref, self.controller))
                or type(self.tapped) is not bool):
            raise GameRuleError("Optional untap subject is malformed")


def _plan_from_dict(value: Any) -> UntapStepPlan:
    names = {field.name for field in fields(UntapStepPlan)}
    if not isinstance(value, Mapping) or set(value) != names:
        raise GameRuleError("Optional untap participation plan is malformed")
    arrays = {name for name in names if name.endswith("_ids")} | {"supporting_source_refs"}
    if any(not isinstance(value[name], list) for name in arrays):
        raise GameRuleError("Optional untap participation arrays are malformed")
    return UntapStepPlan(**{name: tuple(item) if name in arrays else item for name, item in value.items()})


def _held_trigger(value: FrozenMap) -> StackItem:
    raw = thaw_value(value)
    item = StackItem.from_dict(raw)
    if (item.kind != "triggered_ability"
            or any(type(value) is not str or not value for value in (item.stack_id, item.ref, item.controller))
            or thaw_value(FrozenMap(item.to_dict())) != raw):
        raise GameRuleError("Optional untap held trigger is malformed")
    return item


@dataclass(frozen=True, slots=True)
class OptionalUntapContinuation:
    active_player: str
    turn_sequence: int
    phase_index: int
    plan: UntapStepPlan
    rows: tuple[OptionalUntapSubject, ...]
    available_object_ids: tuple[str, ...]
    waiting_triggers: tuple[FrozenMap, ...]

    def __post_init__(self) -> None:
        if (type(self.active_player) is not str or not self.active_player
                or type(self.turn_sequence) is not int or self.turn_sequence < 0
                or type(self.phase_index) is not int or self.phase_index != 0
                or not isinstance(self.plan, UntapStepPlan)
                or self.plan.active_player != self.active_player
                or self.plan.unsupported_source_object_id is not None):
            raise GameRuleError("Optional untap physical step is malformed")
        if (type(self.rows) is not tuple or any(not isinstance(row, OptionalUntapSubject) for row in self.rows)
                or len({row.object_id for row in self.rows}) != len(self.rows)
                or len({row.ref for row in self.rows}) != len(self.rows)
                or len({row.logical_object_id for row in self.rows}) != len(self.rows)):
            raise GameRuleError("Optional untap subject identities are malformed")
        if (type(self.available_object_ids) is not tuple or not self.available_object_ids
                or any(type(value) is not str or not value for value in self.available_object_ids)
                or len(set(self.available_object_ids)) != len(self.available_object_ids)
                or not set(self.available_object_ids).issubset(self.plan.optional_object_ids)):
            raise GameRuleError("Optional untap available subjects are malformed")
        if type(self.waiting_triggers) is not tuple or any(not isinstance(value, FrozenMap) for value in self.waiting_triggers):
            raise GameRuleError("Optional untap held triggers are malformed")
        triggers = tuple(_held_trigger(value) for value in self.waiting_triggers)
        if len({item.stack_id for item in triggers}) != len(triggers) or len({item.ref for item in triggers}) != len(triggers):
            raise GameRuleError("Optional untap held triggers must be unique")

    def to_dict(self) -> dict[str, Any]:
        plan = asdict(self.plan)
        return {
            "schema_version": 1, "active_player": self.active_player,
            "turn_sequence": self.turn_sequence, "phase_index": self.phase_index,
            "phase": "beginning", "step": "untap",
            "plan": {key: list(value) if isinstance(value, tuple) else value for key, value in plan.items()},
            "rows": [asdict(row) for row in self.rows],
            "available_object_ids": list(self.available_object_ids),
            "waiting_triggers": [thaw_value(value) for value in self.waiting_triggers],
        }

    @classmethod
    def from_dict(cls, value: Any) -> OptionalUntapContinuation:
        names = {field.name for field in fields(cls)} | {"schema_version", "phase", "step"}
        if (not isinstance(value, Mapping) or set(value) != names
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["phase"] != "beginning" or value["step"] != "untap"
                or any(not isinstance(value[name], list) for name in ("rows", "available_object_ids", "waiting_triggers"))):
            raise GameRuleError("Optional untap continuation is malformed")
        row_fields = {field.name for field in fields(OptionalUntapSubject)}
        if any(not isinstance(row, Mapping) or set(row) != row_fields for row in value["rows"]):
            raise GameRuleError("Optional untap subject fields are malformed")
        if any(not isinstance(item, Mapping) for item in value["waiting_triggers"]):
            raise GameRuleError("Optional untap held triggers are malformed")
        try:
            return cls(
                active_player=value["active_player"], turn_sequence=value["turn_sequence"],
                phase_index=value["phase_index"], plan=_plan_from_dict(value["plan"]),
                rows=tuple(OptionalUntapSubject(**row) for row in value["rows"]),
                available_object_ids=tuple(value["available_object_ids"]),
                waiting_triggers=tuple(FrozenMap(item) for item in value["waiting_triggers"]),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise GameRuleError("Optional untap retained state is malformed") from exc


def _rows(state: GameState) -> tuple[OptionalUntapSubject, ...]:
    return tuple(
        OptionalUntapSubject(card.object_id, card.logical_object_id, card.ref, card.controller, card.tapped)
        for card in sorted(state.cards.values(), key=lambda value: (value.ref, value.object_id))
        if card.zone == "battlefield" and not card.phased_out
    )


def begin_optional_untap_choice(host: Any, plan: UntapStepPlan, waiting_triggers: list[StackItem]) -> bool:
    if not host.state.config.auto_untap:
        return False
    available = tuple(
        object_id for object_id in plan.optional_object_ids
        if not host.state.cards[object_id].annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION, False)
    )
    if not available:
        return False
    refs = [host.state.cards[object_id].ref for object_id in available]
    frame = OptionalUntapContinuation(
        active_player=plan.active_player, turn_sequence=host.state.turn_sequence,
        phase_index=host.state.phase_index, plan=plan, rows=_rows(host.state),
        available_object_ids=available, waiting_triggers=tuple(FrozenMap(item.to_dict()) for item in waiting_triggers),
    )
    host.state.priority_player = None
    host.permissions.issue(
        kind="untap.optional_source", role="pilot", actors=[plan.active_player], allowed_actions=["choose"],
        payload_by_actor={plan.active_player: {
            "prompt": "Choose which optional permanents to untap.",
            "legal_actions": [{"id": "choose", "action": "choose", "label": "Untap selected permanents",
                "choice_schema": {"refs": {"type": "object_ref_array", "shape": "ref_array",
                    "legal_refs": refs, "minimum": 0, "maximum": len(refs)}}}],
        }}, continuation={"optional_untap": frame.to_dict()},
    )
    return True


def complete_optional_untap_choice(host: Any, decision: Any) -> None:
    frame = OptionalUntapContinuation.from_dict(decision.continuation.get("optional_untap"))
    actor = frame.active_player
    if (decision.kind != "untap.optional_source" or decision.actors != [actor]
            or host.state.active_player != actor or host.state.turn_sequence != frame.turn_sequence
            or host.state.phase_index != frame.phase_index
            or host.state.phase != "beginning" or host.state.step != "untap"):
        raise GameRuleError("Optional untap physical step changed")
    if frame.rows != _rows(host.state):
        raise GameRuleError("Optional untap subjects changed")
    plan = frame.plan
    from .semantic_runtime.untap_steps import current_untap_step_plan
    if plan != current_untap_step_plan(host, actor):
        raise GameRuleError("Optional untap participation changed")
    available = frame.available_object_ids
    expected_available = tuple(object_id for object_id in plan.optional_object_ids
                               if not host.state.cards[object_id].annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION, False))
    if available != expected_available:
        raise GameRuleError("Optional untap available subjects are malformed")
    response = decision.responses.get(actor, {})
    refs = response.get("refs")
    if not isinstance(refs, list) or any(type(ref) is not str for ref in refs) or len(set(refs)) != len(refs):
        raise GameRuleError("Optional untap choice requires unique public references")
    by_ref = {host.state.cards[object_id].ref: object_id for object_id in available}
    if not set(refs).issubset(by_ref):
        raise GameRuleError("Optional untap choice includes an unavailable permanent")
    triggers = [_held_trigger(value) for value in frame.waiting_triggers]
    from .untap_step_coordination import commit_untap_step
    commit_untap_step(host, plan, triggers, selected_optional_ids=tuple(by_ref[ref] for ref in refs))
