from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement import (
    FrozenMap,
    ReplacementEffect,
    ReplacementEffectError,
    ReplacementSelection,
)
from ..replacement.immutable import thaw_value
from .model import (
    DrawError,
    DrawEventRequest,
    DrawnCardAction,
    QueuedDraw,
    drawn_card_action_from_dict,
)


_REASON_FIELD = "reason"


def _exact(value: Mapping[str, Any], fields: set[str], *, name: str) -> None:
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        detail: list[str] = []
        if missing:
            detail.append(f"missing {missing}")
        if unknown:
            detail.append(f"unknown {unknown}")
        raise DrawError(f"{name} fields are invalid: {', '.join(detail)}")


def _string(value: Any, *, name: str, allow_empty: bool = False) -> str:
    if type(value) is not str or (not value and not allow_empty):
        raise DrawError(f"{name} must be a {'string' if allow_empty else 'nonempty string'}")
    return value


@dataclass(frozen=True, slots=True)
class DrawResume:
    kind: str
    seat: str = ""
    stack_ref: str = ""
    effects: tuple[FrozenMap, ...] = ()
    destination: str | None = None
    note: str = ""
    instruction_pointer: int = 0
    draws: tuple[QueuedDraw, ...] = ()
    after: "DrawResume | None" = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "none",
            "turn_draw",
            "semantic_resolution",
            "draw_batch",
        }:
            raise DrawError(f"Unsupported post-draw continuation {self.kind!r}")
        if self.kind == "none":
            if (
                self.seat
                or self.stack_ref
                or self.effects
                or self.destination is not None
                or self.note
                or self.instruction_pointer
                or self.draws
                or self.after is not None
            ):
                raise DrawError("Empty draw continuation carries extra state")
            return
        if self.kind == "turn_draw":
            _string(self.seat, name="Turn-draw continuation seat")
            if (
                self.stack_ref
                or self.effects
                or self.destination is not None
                or self.note
                or self.instruction_pointer
                or self.draws
                or self.after is not None
            ):
                raise DrawError("Turn-draw continuation carries extra state")
            return
        if self.kind == "draw_batch":
            if not self.draws or any(
                not isinstance(draw, QueuedDraw) for draw in self.draws
            ):
                raise DrawError("Draw-batch continuation requires queued draws")
            if (
                self.seat
                or self.stack_ref
                or self.effects
                or self.destination is not None
                or self.note
                or self.instruction_pointer
            ):
                raise DrawError("Draw-batch continuation carries extra state")
            if self.after is not None and not isinstance(
                self.after, DrawResume
            ):
                raise DrawError(
                    "Draw-batch continuation requires a typed final resume"
                )
            return
        _string(self.stack_ref, name="Semantic draw stack reference")
        if self.destination is not None and type(self.destination) is not str:
            raise DrawError("Semantic draw destination must be a string or null")
        _string(self.note, name="Semantic draw note", allow_empty=True)
        if type(self.instruction_pointer) is not int or self.instruction_pointer < 0:
            raise DrawError("Semantic draw instruction pointer must be nonnegative")
        if any(not isinstance(effect, FrozenMap) for effect in self.effects):
            raise DrawError("Semantic draw effects must be immutable objects")
        if self.draws:
            raise DrawError("Semantic draw continuation carries queued draws")
        if self.after is not None:
            raise DrawError("Semantic draw continuation carries a final resume")

    @classmethod
    def none(cls) -> "DrawResume":
        return cls(kind="none")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DrawResume":
        if not isinstance(value, Mapping):
            raise DrawError("Post-draw continuation must be an object")
        kind = value.get("kind")
        if kind == "none":
            _exact(value, {"kind"}, name="Empty draw continuation")
            return cls.none()
        if kind == "turn_draw":
            _exact(value, {"kind", "seat"}, name="Turn-draw continuation")
            return cls(kind="turn_draw", seat=_string(value["seat"], name="Turn-draw continuation seat"))
        if kind == "draw_batch":
            fields = set(value)
            if fields not in (
                {"kind", "draws"},
                {"kind", "draws", "after"},
            ):
                _exact(
                    value,
                    {"kind", "draws", "after"},
                    name="Draw-batch continuation",
                )
            draws = value["draws"]
            if not isinstance(draws, (list, tuple)) or any(
                not isinstance(draw, Mapping) for draw in draws
            ):
                raise DrawError("Draw-batch continuation draws must be objects")
            return cls(
                kind="draw_batch",
                draws=tuple(QueuedDraw.from_dict(draw) for draw in draws),
                after=(
                    cls.from_dict(value["after"])
                    if "after" in value
                    else None
                ),
            )
        if kind != "semantic_resolution":
            raise DrawError("Post-draw continuation kind is invalid")
        fields = {
            "kind",
            "stack_ref",
            "effects",
            "destination",
            "note",
            "instruction_pointer",
        }
        _exact(value, fields, name="Semantic draw continuation")
        effects_value = value["effects"]
        if not isinstance(effects_value, (list, tuple)) or any(
            not isinstance(effect, Mapping) for effect in effects_value
        ):
            raise DrawError("Semantic draw continuation effects must be objects")
        return cls(
            kind="semantic_resolution",
            stack_ref=_string(value["stack_ref"], name="Semantic draw stack reference"),
            effects=tuple(FrozenMap(effect) for effect in effects_value),
            destination=value["destination"],
            note=_string(value["note"], name="Semantic draw note", allow_empty=True),
            instruction_pointer=value["instruction_pointer"],
        )

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "none":
            return {"kind": "none"}
        if self.kind == "turn_draw":
            return {"kind": "turn_draw", "seat": self.seat}
        if self.kind == "draw_batch":
            value = {
                "kind": "draw_batch",
                "draws": [draw.to_dict() for draw in self.draws],
            }
            if self.after is not None:
                value["after"] = self.after.to_dict()
            return value
        return {
            "kind": "semantic_resolution",
            "stack_ref": self.stack_ref,
            "effects": [thaw_value(value) for value in self.effects],
            "destination": self.destination,
            "note": self.note,
            "instruction_pointer": self.instruction_pointer,
        }


@dataclass(frozen=True, slots=True)
class DrawDecisionContinuation:
    event_id: str
    seat: str
    remaining_draws: int
    library_size: int
    reason: str
    private: bool
    effects: tuple[ReplacementEffect, ...]
    selections: tuple[str, ...]
    after: DrawResume
    excluded_effect_ids: tuple[str, ...] = ()
    post_draw_actions: tuple[DrawnCardAction, ...] = ()
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise DrawError("Unsupported draw continuation schema version")
        _string(self.event_id, name="Draw continuation event ID")
        _string(self.seat, name="Draw continuation seat")
        _string(self.reason, name="Draw continuation reason")
        if type(self.remaining_draws) is not int or self.remaining_draws < 1:
            raise DrawError("Draw continuation remaining count must be positive")
        if type(self.library_size) is not int or self.library_size < 0:
            raise DrawError("Draw continuation library size must be nonnegative")
        if type(self.private) is not bool:
            raise DrawError("Draw continuation private flag must be a boolean")
        if not self.effects or any(
            not isinstance(effect, ReplacementEffect) or effect.event_kind != "draw"
            for effect in self.effects
        ):
            raise DrawError("Draw continuation requires draw replacement effects")
        if any(type(selection) is not str or not selection for selection in self.selections):
            raise DrawError("Draw continuation selections must be canonical strings")
        if any(
            type(effect_id) is not str or not effect_id
            for effect_id in self.excluded_effect_ids
        ) or self.excluded_effect_ids != tuple(
            sorted(set(self.excluded_effect_ids))
        ):
            raise DrawError(
                "Draw continuation exclusions must be unique and canonical"
            )
        if self.schema_version == 1 and self.excluded_effect_ids:
            raise DrawError("Legacy draw continuations cannot carry exclusions")
        if any(not isinstance(action, DrawnCardAction) for action in self.post_draw_actions):
            raise DrawError(
                "Draw continuation post-actions must be typed"
            )
        if self.schema_version == 1 and self.post_draw_actions:
            raise DrawError(
                "Legacy draw continuations cannot carry post-actions"
            )
        if not isinstance(self.after, DrawResume):
            raise DrawError("Draw continuation requires a typed resume value")

    @property
    def request(self) -> DrawEventRequest:
        return DrawEventRequest(
            event_id=self.event_id,
            player=self.seat,
            library_size=self.library_size,
            reason=self.reason,
            private=self.private,
            excluded_effect_ids=self.excluded_effect_ids,
            post_draw_actions=self.post_draw_actions,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DrawDecisionContinuation":
        if not isinstance(value, Mapping):
            raise DrawError("Draw decision continuation must be an object")
        schema_version = value.get("schema_version")
        fields = {
                "schema_version",
                "event_id",
                "seat",
                "remaining_draws",
                "library_size",
                _REASON_FIELD,
                "private",
                "effects",
                "selections",
                "after",
            }
        if schema_version == 2:
            fields.add("excluded_effect_ids")
            fields.add("post_draw_actions")
        _exact(value, fields, name="Draw decision continuation")
        effects_value = value["effects"]
        selections_value = value["selections"]
        if not isinstance(effects_value, (list, tuple)) or any(
            not isinstance(effect, Mapping) for effect in effects_value
        ):
            raise DrawError("Draw continuation effects must be objects")
        if not isinstance(selections_value, (list, tuple)):
            raise DrawError("Draw continuation selections must be a list")
        excluded_values = value.get("excluded_effect_ids", ())
        if not isinstance(excluded_values, (list, tuple)):
            raise DrawError("Draw continuation exclusions must be a list")
        post_action_values = value.get("post_draw_actions", ())
        if not isinstance(post_action_values, (list, tuple)) or any(
            not isinstance(action, Mapping) for action in post_action_values
        ):
            raise DrawError("Draw continuation post-actions must be objects")
        try:
            effects = tuple(
                ReplacementEffect.from_dict(effect) for effect in effects_value
            )
        except ReplacementEffectError as exc:
            raise DrawError(str(exc)) from exc
        return cls(
            schema_version=schema_version,
            event_id=value["event_id"],
            seat=value["seat"],
            remaining_draws=value["remaining_draws"],
            library_size=value["library_size"],
            reason=value[_REASON_FIELD],
            private=value["private"],
            effects=effects,
            selections=tuple(selections_value),
            after=DrawResume.from_dict(value["after"]),
            excluded_effect_ids=tuple(excluded_values),
            post_draw_actions=tuple(
                drawn_card_action_from_dict(action)
                for action in post_action_values
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "seat": self.seat,
            "remaining_draws": self.remaining_draws,
            "library_size": self.library_size,
            _REASON_FIELD: self.reason,
            "private": self.private,
            "effects": [effect.to_dict() for effect in self.effects],
            "selections": list(self.selections),
            "after": self.after.to_dict(),
        }
        if self.schema_version == 2:
            value["excluded_effect_ids"] = list(self.excluded_effect_ids)
            value["post_draw_actions"] = [
                action.to_dict() for action in self.post_draw_actions
            ]
        return value


_REVEAL_POLICY_FIELDS = {
    "policy_id",
    "source_object_id",
    "source_ref",
    "source_logical_object_id",
    "source_zone_change_counter",
    "source_controller",
    "player",
    "optional",
}


def _reveal_policy_maps(
    values: tuple[FrozenMap, ...],
    *,
    seat: str,
    optional: bool,
) -> tuple[FrozenMap, ...]:
    result = tuple(
        value if isinstance(value, FrozenMap) else FrozenMap(value)
        for value in values
    )
    identifiers: list[str] = []
    for value in result:
        if set(value) != _REVEAL_POLICY_FIELDS:
            raise DrawError("Draw reveal continuation policy fields are invalid")
        for field in (
            "policy_id",
            "source_object_id",
            "source_ref",
            "source_logical_object_id",
            "source_controller",
            "player",
        ):
            _string(value[field], name=f"Draw reveal policy {field}")
        if value["player"] != seat or value["optional"] is not optional:
            raise DrawError(
                "Draw reveal continuation policy has the wrong seat or kind"
            )
        incarnation = value["source_zone_change_counter"]
        if type(incarnation) is not int or incarnation < 0:
            raise DrawError(
                "Draw reveal continuation source incarnation is invalid"
            )
        identifiers.append(value["policy_id"])
    if len(identifiers) != len(set(identifiers)):
        raise DrawError("Draw reveal continuation policies must be unique")
    return result


@dataclass(frozen=True, slots=True)
class DrawRevealDecisionContinuation:
    event_id: str
    seat: str
    remaining_draws: int
    library_size: int
    drawn_object_id: str
    reason: str
    private: bool
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    after: DrawResume
    mandatory_policies: tuple[FrozenMap, ...]
    optional_policies: tuple[FrozenMap, ...]
    optional_policy_index: int = 0
    selected_policy_ids: tuple[str, ...] = ()
    excluded_effect_ids: tuple[str, ...] = ()
    post_draw_actions: tuple[DrawnCardAction, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise DrawError(
                "Unsupported draw reveal continuation schema version"
            )
        for field_name, value in (
            ("event ID", self.event_id),
            ("seat", self.seat),
            ("drawn object ID", self.drawn_object_id),
            (_REASON_FIELD, self.reason),
        ):
            _string(value, name=f"Draw reveal continuation {field_name}")
        if type(self.remaining_draws) is not int or self.remaining_draws < 1:
            raise DrawError(
                "Draw reveal continuation remaining count must be positive"
            )
        if type(self.library_size) is not int or self.library_size < 1:
            raise DrawError(
                "Draw reveal continuation library size must be positive"
            )
        if type(self.private) is not bool:
            raise DrawError(
                "Draw reveal continuation private flag must be boolean"
            )
        if any(
            not isinstance(effect, ReplacementEffect)
            or effect.event_kind != "draw"
            for effect in self.effects
        ):
            raise DrawError(
                "Draw reveal continuation effects must replace draws"
            )
        if any(
            not isinstance(selection, ReplacementSelection)
            for selection in self.journal
        ):
            raise DrawError(
                "Draw reveal continuation journal must be typed"
            )
        if not isinstance(self.after, DrawResume):
            raise DrawError(
                "Draw reveal continuation requires a typed resume value"
            )
        mandatory = _reveal_policy_maps(
            self.mandatory_policies,
            seat=self.seat,
            optional=False,
        )
        optional = _reveal_policy_maps(
            self.optional_policies,
            seat=self.seat,
            optional=True,
        )
        mandatory_ids = [value["policy_id"] for value in mandatory]
        optional_ids = [value["policy_id"] for value in optional]
        all_ids = [*mandatory_ids, *optional_ids]
        if len(all_ids) != len(set(all_ids)):
            raise DrawError(
                "Draw reveal continuation policy IDs must be globally unique"
            )
        if (
            type(self.optional_policy_index) is not int
            or self.optional_policy_index < 0
            or self.optional_policy_index >= len(optional)
        ):
            raise DrawError(
                "Draw reveal continuation optional-policy index is invalid"
            )
        selected = tuple(self.selected_policy_ids)
        if (
            any(type(value) is not str or not value for value in selected)
            or len(selected) != len(set(selected))
            or any(value not in optional_ids for value in selected)
            or any(
                optional_ids.index(value) >= self.optional_policy_index
                for value in selected
            )
        ):
            raise DrawError(
                "Draw reveal continuation selected policies are invalid"
            )
        exclusions = tuple(self.excluded_effect_ids)
        if (
            any(type(value) is not str or not value for value in exclusions)
            or exclusions != tuple(sorted(set(exclusions)))
        ):
            raise DrawError(
                "Draw reveal continuation exclusions must be canonical"
            )
        if any(not isinstance(action, DrawnCardAction) for action in self.post_draw_actions):
            raise DrawError(
                "Draw reveal continuation post-actions must be typed"
            )
        object.__setattr__(self, "mandatory_policies", mandatory)
        object.__setattr__(self, "optional_policies", optional)
        object.__setattr__(self, "selected_policy_ids", selected)
        object.__setattr__(self, "excluded_effect_ids", exclusions)

    @property
    def request(self) -> DrawEventRequest:
        return DrawEventRequest(
            event_id=self.event_id,
            player=self.seat,
            library_size=self.library_size,
            reason=self.reason,
            private=self.private,
            excluded_effect_ids=self.excluded_effect_ids,
            post_draw_actions=self.post_draw_actions,
        )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "DrawRevealDecisionContinuation":
        if not isinstance(value, Mapping):
            raise DrawError(
                "Draw reveal decision continuation must be an object"
            )
        fields = {
            "schema_version",
            "event_id",
            "seat",
            "remaining_draws",
            "library_size",
            "drawn_object_id",
            _REASON_FIELD,
            "private",
            "effects",
            "journal",
            "after",
            "mandatory_policies",
            "optional_policies",
            "optional_policy_index",
            "selected_policy_ids",
            "excluded_effect_ids",
            "post_draw_actions",
        }
        _exact(value, fields, name="Draw reveal decision continuation")
        mapping_lists = (
            "effects",
            "journal",
            "mandatory_policies",
            "optional_policies",
            "post_draw_actions",
        )
        for field in mapping_lists:
            values = value[field]
            if not isinstance(values, (list, tuple)) or any(
                not isinstance(item, Mapping) for item in values
            ):
                raise DrawError(
                    f"Draw reveal continuation {field} must contain objects"
                )
        for field in ("selected_policy_ids", "excluded_effect_ids"):
            if not isinstance(value[field], (list, tuple)):
                raise DrawError(
                    f"Draw reveal continuation {field} must be a list"
                )
        try:
            effects = tuple(
                ReplacementEffect.from_dict(effect)
                for effect in value["effects"]
            )
            journal = tuple(
                ReplacementSelection.from_dict(selection)
                for selection in value["journal"]
            )
        except ReplacementEffectError as exc:
            raise DrawError(str(exc)) from exc
        return cls(
            schema_version=value["schema_version"],
            event_id=value["event_id"],
            seat=value["seat"],
            remaining_draws=value["remaining_draws"],
            library_size=value["library_size"],
            drawn_object_id=value["drawn_object_id"],
            reason=value[_REASON_FIELD],
            private=value["private"],
            effects=effects,
            journal=journal,
            after=DrawResume.from_dict(value["after"]),
            mandatory_policies=tuple(
                FrozenMap(item) for item in value["mandatory_policies"]
            ),
            optional_policies=tuple(
                FrozenMap(item) for item in value["optional_policies"]
            ),
            optional_policy_index=value["optional_policy_index"],
            selected_policy_ids=tuple(value["selected_policy_ids"]),
            excluded_effect_ids=tuple(value["excluded_effect_ids"]),
            post_draw_actions=tuple(
                drawn_card_action_from_dict(action)
                for action in value["post_draw_actions"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "seat": self.seat,
            "remaining_draws": self.remaining_draws,
            "library_size": self.library_size,
            "drawn_object_id": self.drawn_object_id,
            _REASON_FIELD: self.reason,
            "private": self.private,
            "effects": [effect.to_dict() for effect in self.effects],
            "journal": [selection.to_dict() for selection in self.journal],
            "after": self.after.to_dict(),
            "mandatory_policies": [
                thaw_value(value) for value in self.mandatory_policies
            ],
            "optional_policies": [
                thaw_value(value) for value in self.optional_policies
            ],
            "optional_policy_index": self.optional_policy_index,
            "selected_policy_ids": list(self.selected_policy_ids),
            "excluded_effect_ids": list(self.excluded_effect_ids),
            "post_draw_actions": [
                action.to_dict() for action in self.post_draw_actions
            ],
        }


__all__ = [
    "DrawDecisionContinuation",
    "DrawResume",
    "DrawRevealDecisionContinuation",
]
