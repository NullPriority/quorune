from __future__ import annotations

"""Closed staged Assist payments, stored without a provisional game snapshot."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re

from ...errors import GameRuleError
from ...replacement.immutable import FrozenMap, thaw_value
from .assist_costs import ASSIST_HELPER_FIELD


ASSIST_FRAME_FIELD = "_assist_frame"
ASSIST_PREVIEW_FIELD = "_assist_preview"
ASSIST_CASTER_PAYMENT_FIELD = "_assist_caster_payment"
ASSIST_DECISION_KIND = "cast.assist"
ASSIST_INTERNAL_FIELDS = frozenset({ASSIST_FRAME_FIELD, ASSIST_PREVIEW_FIELD, ASSIST_CASTER_PAYMENT_FIELD})

_MANA_STAGES = frozenset({"helper_mana", "caster_mana"})
_MANA_COLORS = frozenset("WUBRGC")
_FRAME_FIELDS = frozenset({
    "schema_version", "caster", "helper", "stage", "source_ref", "fingerprint",
    "priority_frame", "cast_response", "plans", "pending", "contribution",
})
_STEP_FIELDS = frozenset({"seat", "source", "bundle", "tap_source", "replacements"})


def _closed_mapping(value, fields, message):
    if not isinstance(value, Mapping) or set(value) != fields:
        raise GameRuleError(message)
    return value


def assist_mana_bundle(value, *, allow_zero=True):
    if not isinstance(value, Mapping) or any(
        type(color) is not str or color not in _MANA_COLORS or type(amount) is not int or amount < 0
        for color, amount in value.items()
    ):
        raise GameRuleError("Assist requires an exact mana bundle")
    bundle = {color: amount for color, amount in value.items() if amount}
    if not allow_zero and not bundle:
        raise GameRuleError("An Assist mana activation must produce mana")
    return FrozenMap(bundle)


@dataclass(frozen=True, slots=True)
class AssistManaStep:
    seat: str
    source: str
    bundle: FrozenMap
    tap_source: bool
    replacements: FrozenMap = FrozenMap({})

    def __post_init__(self):
        if (type(self.seat) is not str or not self.seat or type(self.source) is not str or not self.source
                or type(self.tap_source) is not bool or not isinstance(self.replacements, Mapping)):
            raise GameRuleError("Assist mana activation is malformed")
        for event_id, selections in self.replacements.items():
            if (type(event_id) is not str or not event_id or not isinstance(selections, (list, tuple))
                    or not selections or any(not ((type(value) is str and value) or isinstance(value, Mapping))
                                            for value in selections)):
                raise GameRuleError("Assist mana replacement journal is malformed")
        object.__setattr__(self, "bundle", assist_mana_bundle(self.bundle, allow_zero=False))
        object.__setattr__(self, "replacements", FrozenMap(self.replacements))

    @classmethod
    def from_dict(cls, value):
        return cls(**_closed_mapping(value, _STEP_FIELDS, "Assist mana activation fields changed"))

    def activation(self):
        return {"source": self.source, "bundle": thaw_value(self.bundle), "tap_source": self.tap_source}

    def to_dict(self):
        return {"seat": self.seat, **self.activation(), "replacements": thaw_value(self.replacements)}


@dataclass(frozen=True, slots=True)
class PendingAssistReplacement:
    step: AssistManaStep
    event_id: str

    def __post_init__(self):
        if not isinstance(self.step, AssistManaStep) or type(self.event_id) is not str or not self.event_id:
            raise GameRuleError("Assist mana replacement identity is malformed")

    @classmethod
    def from_dict(cls, value):
        raw = _closed_mapping(value, {"step", "event_id"}, "Assist replacement fields changed")
        return cls(AssistManaStep.from_dict(raw["step"]), raw["event_id"])

    def to_dict(self):
        return {"step": self.step.to_dict(), "event_id": self.event_id}


@dataclass(frozen=True, slots=True)
class AssistCastFrame:
    caster: str
    helper: str
    source_ref: str
    fingerprint: str
    priority_frame: FrozenMap
    cast_response: FrozenMap
    stage: str = "helper_mana"
    plans: tuple[AssistManaStep, ...] = ()
    pending: PendingAssistReplacement | None = None
    contribution: FrozenMap | None = None
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or any(type(value) is not str or not value for value in (self.caster, self.helper, self.source_ref))
            or self.caster == self.helper
            or type(self.stage) is not str
            or self.stage not in {"helper_mana", "caster_mana", "helper_payment", "caster_payment"}
            or type(self.fingerprint) is not str or not re.fullmatch(r"[a-f0-9]{64}", self.fingerprint)
            or not isinstance(self.priority_frame, Mapping) or not isinstance(self.cast_response, Mapping)
        ):
            raise GameRuleError("Assist cast frame is malformed")
        if (
            self.cast_response.get("card") != self.source_ref
            or self.cast_response.get(ASSIST_HELPER_FIELD) != self.helper
            or not isinstance(self.cast_response.get("_mana_payment_id"), str)
            or not self.cast_response["_mana_payment_id"]
            or ASSIST_INTERNAL_FIELDS.intersection(self.cast_response)
            or self.priority_frame.get("priority_player") != self.caster
        ):
            raise GameRuleError("Assist cast frame does not bind the original action")
        if not isinstance(self.plans, (tuple, list)) or any(not isinstance(step, AssistManaStep) for step in self.plans):
            raise GameRuleError("Assist mana plans are malformed")
        seats = [step.seat for step in self.plans]
        if seats != [self.helper] * seats.count(self.helper) + [self.caster] * seats.count(self.caster):
            raise GameRuleError("Assist mana windows changed order")
        if self.stage == "helper_mana" and self.caster in seats:
            raise GameRuleError("Caster mana cannot precede the helper's mana window")
        if self.pending is not None and (
            not isinstance(self.pending, PendingAssistReplacement)
            or self.stage not in _MANA_STAGES or self.pending.step.seat != self.actor
        ):
            raise GameRuleError("Assist replacement is outside its mana window")
        if (self.contribution is not None) != (self.stage == "caster_payment"):
            raise GameRuleError("Assist contribution does not match its payment stage")
        if self.contribution is not None:
            object.__setattr__(self, "contribution", assist_mana_bundle(self.contribution))
        object.__setattr__(self, "priority_frame", FrozenMap(self.priority_frame))
        object.__setattr__(self, "cast_response", FrozenMap(self.cast_response))
        object.__setattr__(self, "plans", tuple(self.plans))

    @property
    def actor(self):
        return self.helper if self.stage in {"helper_mana", "helper_payment"} else self.caster

    @property
    def payment_id(self):
        return self.cast_response["_mana_payment_id"]

    def step_payment_id(self, index):
        return f"{self.payment_id}:assist:{index}"

    @classmethod
    def from_dict(cls, value):
        raw = _closed_mapping(value, _FRAME_FIELDS, "Assist cast frame fields changed")
        plans = raw["plans"]
        if not isinstance(plans, (tuple, list)):
            raise GameRuleError("Assist mana plans are malformed")
        return cls(**{**raw, "plans": tuple(AssistManaStep.from_dict(step) for step in plans),
            "pending": None if raw["pending"] is None else PendingAssistReplacement.from_dict(raw["pending"])})

    def to_dict(self):
        return {
            "schema_version": self.schema_version, "caster": self.caster, "helper": self.helper,
            "source_ref": self.source_ref, "fingerprint": self.fingerprint, "stage": self.stage,
            "priority_frame": thaw_value(self.priority_frame), "cast_response": thaw_value(self.cast_response),
            "plans": [step.to_dict() for step in self.plans],
            "pending": None if self.pending is None else self.pending.to_dict(),
            "contribution": None if self.contribution is None else thaw_value(self.contribution),
        }
