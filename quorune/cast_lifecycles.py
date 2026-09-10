from __future__ import annotations

"""Typed fixed casting lifecycles and their post-resolution transitions."""

import copy
from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Mapping, Protocol, Sequence

from .additional_cost_vocabulary import (
    DISCARD_ONE_COST,
    EXILE_ONE_FROM_GRAVEYARD_COST,
)
from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .continuous_effect_state import ResolutionEffectSource
from .continuous_effect_model import ContinuousEffectDuration
from .impulse_access_model import (
    ImpulseAccessDuration,
    TemporaryCastPermissionGrant,
)
from .model import CardInstance, StackItem
from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value
from .replacement.model import ReplacementClass, ReplacementEffect
from .replacement.operations import SetField
from .rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from .trigger_processing import schedule_delayed_trigger
from .util import mana_cost_to_vector, stable_json
from .zone_object_keyword_grants import commit_zone_object_keyword_grant
from .zone_object_keyword_model import normalized_zone_object_keyword
from .zone_trigger_events import ZoneTransitionKind


FIXED_CAST_LIFECYCLE_CAPABILITY_ID = "casting.lifecycle.fixed_public"
FIXED_ZONE_CAST_LIFECYCLE_CAPABILITY_ID = "casting.lifecycle.fixed_zone_cast"
FIXED_CAST_LIFECYCLE_HANDLER_ID = "casting.lifecycle.fixed-public.v1"
FIXED_CAST_LIFECYCLE_RUNTIME_EVENT = "cast.cost"
FIXED_CAST_LIFECYCLE_CONTEXT_FIELD = "fixed_cast_lifecycle"
FIXED_CAST_LIFECYCLE_STACK_ANNOTATION = "fixed_cast_lifecycle_stack"
FIXED_ZONE_CAST_DESIGNATION_FIELD = "fixed_zone_cast_designation"
SUSPEND_HASTE_CONTEXT_FIELD = "suspend_haste"
REBOUND_CAST_SEMANTIC_KEY = "builtin:rebound-cast-choice"
REBOUND_EXILE_CAST_PRODUCER = "rebound"


class FixedCastLifecycleKind(str, Enum):
    BUYBACK = "buyback"
    DASH = "dash"
    ESCAPE = "escape"
    FORETELL = "foretell"
    JUMP_START = "jump-start"
    MADNESS = "madness"
    PLOT = "plot"
    REBOUND = "rebound"
    WARP = "warp"
    RETRACE = "retrace"
    SUSPEND = "suspend"


_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_ORDINARY_COST = r"(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+"
_FIXED_LIFECYCLE = re.compile(
    rf"^(?P<mechanic>Buyback|Dash|Madness|Warp) (?P<cost>{_ORDINARY_COST})"
    r"(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_RETRACE = re.compile(
    r"^Retrace(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_SUSPEND = re.compile(
    rf"^Suspend (?P<count>[1-9][0-9]*)[\-\u2013\u2014\ufffd]"
    rf"(?P<cost>{_ORDINARY_COST})(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_FORETELL = re.compile(
    rf"^Foretell (?P<cost>{_ORDINARY_COST})(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_PLOT = re.compile(
    rf"^Plot (?P<cost>{_ORDINARY_COST})(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_ESCAPE = re.compile(
    rf"^Escape[\-\u2013\u2014\ufffd](?P<cost>{_ORDINARY_COST}),\s*"
    r"Exile (?P<count>one|two|three|four|five|six|seven|eight|nine|ten|"
    r"[1-9][0-9]*) other cards from your graveyard\."
    r"(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_REBOUND = re.compile(
    r"^Rebound(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_JUMP_START = re.compile(
    r"^Jump-start(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_FIXED_COUNTS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


class FixedCastLifecycleError(ValueError):
    """A fixed casting-lifecycle descriptor or transition is malformed."""


def _validate_fixed_lifecycle_mana(spec: Any, match: re.Match[str]) -> None:
    if (
        type(spec.cost_text) is not str
        or re.fullmatch(_ORDINARY_COST, spec.cost_text) is None
        or match.group("cost").upper() != spec.cost_text
    ):
        raise FixedCastLifecycleError(
            "Fixed cast lifecycle requires matching ordinary mana"
        )
    if not isinstance(spec.mana_cost, FrozenMap):
        if not isinstance(spec.mana_cost, Mapping):
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle mana cost must be an object"
            )
        object.__setattr__(spec, "mana_cost", FrozenMap(spec.mana_cost))
    mana = thaw_value(spec.mana_cost)
    expected, complex_symbols = mana_cost_to_vector(spec.cost_text)
    if (
        set(mana) != set(_MANA_FIELDS)
        or any(type(value) is not int or value < 0 for value in mana.values())
        or complex_symbols
        or mana != expected
    ):
        raise FixedCastLifecycleError(
            "Fixed cast-lifecycle mana vector does not match its cost"
        )


def _validate_fixed_zone_cast_spec(spec: Any) -> None:
    if spec.kind in {
        FixedCastLifecycleKind.JUMP_START,
        FixedCastLifecycleKind.REBOUND,
    }:
        pattern = (
            _JUMP_START
            if spec.kind is FixedCastLifecycleKind.JUMP_START
            else _REBOUND
        )
        if (
            pattern.fullmatch(spec.oracle_line.strip()) is None
            or spec.cost_text is not None
            or spec.mana_cost is not None
            or spec.counter_count is not None
            or spec.exile_count is not None
            or spec.schema_version != 3
        ):
            raise FixedCastLifecycleError(
                f"{spec.kind.value.title()} requires its closed bare declaration"
            )
        return
    pattern = {
        FixedCastLifecycleKind.ESCAPE: _ESCAPE,
        FixedCastLifecycleKind.FORETELL: _FORETELL,
        FixedCastLifecycleKind.PLOT: _PLOT,
    }[spec.kind]
    match = pattern.fullmatch(spec.oracle_line.strip())
    escape_count = (
        _fixed_count(match.group("count"))
        if match is not None and spec.kind is FixedCastLifecycleKind.ESCAPE
        else None
    )
    count_is_valid = (
        type(spec.exile_count) is int
        and spec.exile_count > 0
        and spec.exile_count == escape_count
        if spec.kind is FixedCastLifecycleKind.ESCAPE
        else spec.exile_count is None
    )
    if (
        match is None
        or spec.schema_version != 3
        or spec.counter_count is not None
        or not count_is_valid
    ):
        raise FixedCastLifecycleError(
            "Fixed zone-cast lifecycle declaration is malformed"
        )
    _validate_fixed_lifecycle_mana(spec, match)


@dataclass(frozen=True, slots=True)
class FixedCastLifecycleSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    kind: FixedCastLifecycleKind
    cost_text: str | None
    mana_cost: FrozenMap | None
    counter_count: int | None = None
    exile_count: int | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            1,
            2,
            3,
        }:
            raise FixedCastLifecycleError(
                "Unsupported fixed cast-lifecycle schema version"
            )
        if _ABILITY_ID.fullmatch(self.ability_id) is None:
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle ability ID must be abN"
            )
        if type(self.line_index) is not int or self.line_index < 0:
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle line index must be nonnegative"
            )
        if self.ability_id != f"ab{self.line_index + 1}":
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle ability ID does not match its source line"
            )
        if type(self.oracle_line) is not str or not self.oracle_line:
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle Oracle line is required"
            )
        if not isinstance(self.kind, FixedCastLifecycleKind):
            raise FixedCastLifecycleError(
                "Fixed cast lifecycle kind is unsupported"
            )
        if self.kind in {
            FixedCastLifecycleKind.ESCAPE,
            FixedCastLifecycleKind.FORETELL,
            FixedCastLifecycleKind.JUMP_START,
            FixedCastLifecycleKind.PLOT,
            FixedCastLifecycleKind.REBOUND,
        }:
            _validate_fixed_zone_cast_spec(self)
            return
        if self.kind is FixedCastLifecycleKind.RETRACE:
            if _RETRACE.fullmatch(self.oracle_line.strip()) is None:
                raise FixedCastLifecycleError(
                    "Retrace Oracle line is outside the closed grammar"
                )
            if (
                self.cost_text is not None
                or self.mana_cost is not None
                or self.counter_count is not None
                or self.exile_count is not None
                or self.schema_version != 1
            ):
                raise FixedCastLifecycleError(
                    "Retrace uses the printed cost and has no fixed mana field"
                )
            return
        if self.kind is FixedCastLifecycleKind.SUSPEND:
            match = _SUSPEND.fullmatch(self.oracle_line.strip())
            if (
                match is None
                or self.schema_version != 2
                or type(self.counter_count) is not int
                or self.counter_count <= 0
                or int(match.group("count")) != self.counter_count
                or self.exile_count is not None
            ):
                raise FixedCastLifecycleError(
                    "Suspend requires a positive fixed counter count"
                )
        else:
            match = _FIXED_LIFECYCLE.fullmatch(self.oracle_line.strip())
        if (
            match is None
            or (
                self.kind is not FixedCastLifecycleKind.SUSPEND
                and match.group("mechanic").casefold() != self.kind.value
            )
        ):
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle Oracle line does not match its kind"
            )
        if (
            self.kind is not FixedCastLifecycleKind.SUSPEND
            and (
                self.counter_count is not None
                or self.exile_count is not None
                or self.schema_version != 1
            )
        ):
            raise FixedCastLifecycleError(
                "Only Suspend carries a counter count"
            )
        _validate_fixed_lifecycle_mana(self, match)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(stable_json(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "FixedCastLifecycleSpec":
        legacy = {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "kind",
            "cost_text",
            "mana_cost",
        }
        current = legacy | {"counter_count"}
        staged = current | {"exile_count"}
        if not isinstance(value, Mapping) or set(value) not in {
            frozenset(legacy),
            frozenset(current),
            frozenset(staged),
        }:
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle descriptors have a closed schema"
            )
        raw_mana = value["mana_cost"]
        if raw_mana is not None and not isinstance(raw_mana, Mapping):
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle mana cost must be an object or null"
            )
        try:
            kind = FixedCastLifecycleKind(value["kind"])
        except (TypeError, ValueError) as exc:
            raise FixedCastLifecycleError(
                "Fixed cast lifecycle kind is unsupported"
            ) from exc
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            kind=kind,
            cost_text=value["cost_text"],
            mana_cost=(FrozenMap(raw_mana) if raw_mana is not None else None),
            counter_count=value.get("counter_count"),
            exile_count=value.get("exile_count"),
            schema_version=value["schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "kind": self.kind.value,
            "cost_text": self.cost_text,
            "mana_cost": (
                thaw_value(self.mana_cost)
                if self.mana_cost is not None
                else None
            ),
        }
        if self.schema_version >= 2:
            value["counter_count"] = self.counter_count
        if self.schema_version == 3:
            value["exile_count"] = self.exile_count
        return value

    def fixed_cost_option(self) -> dict[str, Any]:
        if self.kind in {
            FixedCastLifecycleKind.RETRACE,
            FixedCastLifecycleKind.SUSPEND,
            FixedCastLifecycleKind.JUMP_START,
            FixedCastLifecycleKind.REBOUND,
        } or self.mana_cost is None:
            raise FixedCastLifecycleError(
                "This lifecycle does not define a fixed-mana cast option"
            )
        option = {
            "id": self.kind.value,
            "kind": (
                "optional_additional"
                if self.kind is FixedCastLifecycleKind.BUYBACK
                else "alternate"
            ),
            "label": f"{self.kind.value.title()} {self.cost_text}",
            "requirements": (
                {field: 0 for field in _MANA_FIELDS}
                if self.kind is FixedCastLifecycleKind.PLOT
                else thaw_value(self.mana_cost)
            ),
            FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: self.to_dict(),
            "fixed_cast_lifecycle_fingerprint": self.fingerprint,
        }
        if self.kind is FixedCastLifecycleKind.WARP:
            option["source_zone"] = "hand"
        if self.kind in {
            FixedCastLifecycleKind.FORETELL,
            FixedCastLifecycleKind.PLOT,
        }:
            option["source_zone"] = "exile"
        if self.kind is FixedCastLifecycleKind.ESCAPE:
            option["source_zone"] = "graveyard"
            option["_additional_option_costs"] = [
                escape_other_card_cost_descriptor(self.exile_count or 0)
            ]
        if self.kind in {
            FixedCastLifecycleKind.DASH,
            FixedCastLifecycleKind.ESCAPE,
            FixedCastLifecycleKind.FORETELL,
            FixedCastLifecycleKind.MADNESS,
            FixedCastLifecycleKind.PLOT,
            FixedCastLifecycleKind.WARP,
        }:
            option["x_value_policy"] = "zero"
        if self.kind is FixedCastLifecycleKind.MADNESS:
            option["source_zone"] = "exile"
        return option

    def printed_zone_cost_option(
        self,
        base_option: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.kind not in {
            FixedCastLifecycleKind.JUMP_START,
            FixedCastLifecycleKind.RETRACE,
        }:
            raise FixedCastLifecycleError(
                "This lifecycle does not decorate a printed cost"
            )
        option = copy.deepcopy(dict(base_option))
        base_id = str(option.get("id") or "normal")
        option.update(
            {
                "id": (
                    self.kind.value
                    if base_id == "normal"
                    else f"{self.kind.value}:{base_id}"
                ),
                "kind": "zone_cast",
                "label": self.kind.value.title(),
                "base_cost_option": base_id,
                FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: self.to_dict(),
                "fixed_cast_lifecycle_fingerprint": self.fingerprint,
                "source_zone": "graveyard",
                "_additional_option_costs": [
                    retrace_land_discard_cost_descriptor()
                    if self.kind is FixedCastLifecycleKind.RETRACE
                    else jump_start_discard_cost_descriptor()
                ],
            }
        )
        return option

    def retrace_cost_option(
        self,
        base_option: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.kind is not FixedCastLifecycleKind.RETRACE:
            raise FixedCastLifecycleError(
                "Only Retrace decorates a printed cost option"
            )
        return self.printed_zone_cost_option(base_option)


def _fixed_count(value: str) -> int | None:
    normalized = value.casefold()
    if normalized.isdecimal():
        return int(normalized)
    return _FIXED_COUNTS.get(normalized)


def compile_fixed_cast_lifecycle(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> FixedCastLifecycleSpec | None:
    """Compile a fixed-mana public lifecycle or ordinary Retrace."""

    normalized = " ".join(material_line.strip().split())
    for pattern, kind in (
        (_FORETELL, FixedCastLifecycleKind.FORETELL),
        (_PLOT, FixedCastLifecycleKind.PLOT),
        (_ESCAPE, FixedCastLifecycleKind.ESCAPE),
    ):
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        cost_text = match.group("cost").upper()
        mana_cost, complex_symbols = mana_cost_to_vector(cost_text)
        if complex_symbols:
            return None
        return FixedCastLifecycleSpec(
            ability_id=f"ab{line_index + 1}",
            line_index=line_index,
            oracle_line=oracle_line,
            kind=kind,
            cost_text=cost_text,
            mana_cost=FrozenMap(mana_cost),
            exile_count=(
                _fixed_count(match.group("count"))
                if kind is FixedCastLifecycleKind.ESCAPE
                else None
            ),
            schema_version=3,
        )
    for pattern, kind in (
        (_JUMP_START, FixedCastLifecycleKind.JUMP_START),
        (_REBOUND, FixedCastLifecycleKind.REBOUND),
    ):
        if pattern.fullmatch(normalized) is not None:
            return FixedCastLifecycleSpec(
                ability_id=f"ab{line_index + 1}",
                line_index=line_index,
                oracle_line=oracle_line,
                kind=kind,
                cost_text=None,
                mana_cost=None,
                schema_version=3,
            )
    suspended = _SUSPEND.fullmatch(normalized)
    if suspended is not None:
        cost_text = suspended.group("cost").upper()
        mana_cost, complex_symbols = mana_cost_to_vector(cost_text)
        if complex_symbols:
            return None
        return FixedCastLifecycleSpec(
            ability_id=f"ab{line_index + 1}",
            line_index=line_index,
            oracle_line=oracle_line,
            kind=FixedCastLifecycleKind.SUSPEND,
            cost_text=cost_text,
            mana_cost=FrozenMap(mana_cost),
            counter_count=int(suspended.group("count")),
            schema_version=2,
        )
    fixed = _FIXED_LIFECYCLE.fullmatch(normalized)
    if fixed is not None:
        cost_text = fixed.group("cost").upper()
        mana_cost, complex_symbols = mana_cost_to_vector(cost_text)
        if complex_symbols:
            return None
        return FixedCastLifecycleSpec(
            ability_id=f"ab{line_index + 1}",
            line_index=line_index,
            oracle_line=oracle_line,
            kind=FixedCastLifecycleKind(
                fixed.group("mechanic").casefold()
            ),
            cost_text=cost_text,
            mana_cost=FrozenMap(mana_cost),
        )
    if _RETRACE.fullmatch(normalized) is not None:
        return FixedCastLifecycleSpec(
            ability_id=f"ab{line_index + 1}",
            line_index=line_index,
            oracle_line=oracle_line,
            kind=FixedCastLifecycleKind.RETRACE,
            cost_text=None,
            mana_cost=None,
        )
    return None


def fixed_cast_lifecycle_handler_descriptor(
    spec: FixedCastLifecycleSpec,
) -> dict[str, Any]:
    return {
        "handler_id": FIXED_CAST_LIFECYCLE_HANDLER_ID,
        "schema_version": 1,
        "event": FIXED_CAST_LIFECYCLE_RUNTIME_EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "lifecycle": spec.to_dict(),
    }


def retrace_land_discard_cost_descriptor() -> dict[str, Any]:
    return FixedZoneChangeAdditionalCost(
        operation=DISCARD_ONE_COST,
        choice_field="discard_cards",
        predicate=ObjectQuerySpec(
            zones=("hand",),
            owner="$actor",
            types_all=("land",),
            known_to_actor=True,
        ),
    ).to_descriptor()


def jump_start_discard_cost_descriptor() -> dict[str, Any]:
    return FixedZoneChangeAdditionalCost(
        operation=DISCARD_ONE_COST,
        choice_field="discard_cards",
        predicate=ObjectQuerySpec(
            zones=("hand",),
            owner="$actor",
            known_to_actor=True,
        ),
    ).to_descriptor()


def escape_other_card_cost_descriptor(count: int) -> dict[str, Any]:
    return FixedZoneChangeAdditionalCost(
        operation=EXILE_ONE_FROM_GRAVEYARD_COST,
        choice_field="exile_cards",
        predicate=ObjectQuerySpec(
            zones=("graveyard",),
            owner="$actor",
            known_to_actor=True,
        ),
        count=count,
    ).to_descriptor()


@dataclass(frozen=True, slots=True)
class FixedZoneCastDesignation:
    """One identity-pinned Foretell or Plot designation in exile."""

    lifecycle: FixedCastLifecycleSpec
    logical_object_id: str
    created_turn_sequence: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedCastLifecycleError(
                "Fixed zone-cast designation version is unsupported"
            )
        if self.lifecycle.kind not in {
            FixedCastLifecycleKind.FORETELL,
            FixedCastLifecycleKind.PLOT,
        }:
            raise FixedCastLifecycleError(
                "Only Foretell or Plot may create a staged designation"
            )
        if type(self.logical_object_id) is not str or not self.logical_object_id:
            raise FixedCastLifecycleError(
                "Fixed zone-cast designation requires a logical object"
            )
        if (
            type(self.created_turn_sequence) is not int
            or self.created_turn_sequence < 0
        ):
            raise FixedCastLifecycleError(
                "Fixed zone-cast designation requires a turn sequence"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "lifecycle": self.lifecycle.to_dict(),
            "logical_object_id": self.logical_object_id,
            "created_turn_sequence": self.created_turn_sequence,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "FixedZoneCastDesignation":
        expected = {
            "schema_version",
            "lifecycle",
            "logical_object_id",
            "created_turn_sequence",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise FixedCastLifecycleError(
                "Fixed zone-cast designation fields are closed"
            )
        lifecycle = value["lifecycle"]
        if not isinstance(lifecycle, Mapping):
            raise FixedCastLifecycleError(
                "Fixed zone-cast designation lifecycle is malformed"
            )
        return cls(
            schema_version=value["schema_version"],
            lifecycle=FixedCastLifecycleSpec.from_dict(lifecycle),
            logical_object_id=value["logical_object_id"],
            created_turn_sequence=value["created_turn_sequence"],
        )


def fixed_zone_cast_designation(
    state: Any,
    card: CardInstance,
    *,
    actor: str,
) -> FixedZoneCastDesignation | None:
    raw = card.annotations.get(FIXED_ZONE_CAST_DESIGNATION_FIELD)
    if not isinstance(raw, Mapping):
        return None
    try:
        designation = FixedZoneCastDesignation.from_dict(raw)
    except (FixedCastLifecycleError, TypeError, ValueError):
        return None
    if (
        card.zone != "exile"
        or card.object_kind != "card"
        or card.owner != actor
        or designation.logical_object_id != card.logical_object_id
        or state.turn_sequence <= designation.created_turn_sequence
        or (
            designation.lifecycle.kind is FixedCastLifecycleKind.FORETELL
            and (not card.face_down or card.known_to != [card.owner])
        )
        or (
            designation.lifecycle.kind is FixedCastLifecycleKind.PLOT
            and card.face_down
        )
    ):
        return None
    return designation


def fixed_zone_cast_timing_is_legal(
    state: Any,
    designation: FixedZoneCastDesignation,
    *,
    actor: str,
) -> bool:
    if designation.lifecycle.kind is not FixedCastLifecycleKind.PLOT:
        return True
    return bool(
        state.active_player == actor
        and (state.phase, state.step)
        in {("precombat_main", "main"), ("postcombat_main", "main")}
        and not state.stack
    )


class FixedCastLifecycleHost(Protocol):
    state: Any
    seats: Sequence[str]

    def move_card(self, object_id: str, zone: str, **kwargs: Any) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...


def fixed_cast_lifecycle_resolution_destination(
    item: StackItem,
    destination: str | None,
) -> str | None:
    """Apply lifecycle destinations that are not replacement choices."""

    raw = item.context.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if not isinstance(raw, Mapping):
        return destination
    spec = FixedCastLifecycleSpec.from_dict(raw)
    if spec.kind is FixedCastLifecycleKind.JUMP_START:
        return "exile"
    if (
        spec.kind is FixedCastLifecycleKind.BUYBACK
        and destination in {None, "graveyard"}
    ):
        return "hand"
    return destination


def fixed_cast_lifecycle_stack_fields(
    selected_option: Mapping[str, Any],
    destination: str | None,
    *,
    rebound: FixedCastLifecycleSpec | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Return the initial destination and immutable stack lifecycle context."""

    raw = selected_option.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if not isinstance(raw, Mapping):
        if rebound is None:
            return destination, {}
        if rebound.kind is not FixedCastLifecycleKind.REBOUND:
            raise FixedCastLifecycleError(
                "Intrinsic stack lifecycle is not Rebound"
            )
        return destination, {
            FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: rebound.to_dict(),
            "rebound_from_hand": True,
        }
    spec = FixedCastLifecycleSpec.from_dict(raw)
    if (
        spec.kind is FixedCastLifecycleKind.BUYBACK
        and destination == "graveyard"
    ):
        destination = "hand"
    return destination, {
        FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: spec.to_dict()
    }


def fixed_cast_lifecycle_replacement_effect_id(
    kind: FixedCastLifecycleKind,
    logical_object_id: str,
) -> str:
    if kind not in {
        FixedCastLifecycleKind.JUMP_START,
        FixedCastLifecycleKind.REBOUND,
    }:
        raise FixedCastLifecycleError(
            "This cast lifecycle has no stack replacement identity"
        )
    if type(logical_object_id) is not str or not logical_object_id:
        raise FixedCastLifecycleError(
            "A cast-lifecycle replacement requires a logical object"
        )
    return f"rule:{kind.value}:{logical_object_id}"


def fixed_cast_lifecycle_stack_replacement(
    card: CardInstance | None,
) -> ReplacementEffect | None:
    """Return a represented lifecycle's stack-zone replacement."""

    if (
        not isinstance(card, CardInstance)
        or card.zone != "stack"
        or card.object_kind != "card"
    ):
        return None
    raw = card.annotations.get(FIXED_CAST_LIFECYCLE_STACK_ANNOTATION)
    if not isinstance(raw, Mapping):
        return None
    try:
        spec = FixedCastLifecycleSpec.from_dict(raw)
    except (FixedCastLifecycleError, TypeError, ValueError):
        return None
    if spec.kind not in {
        FixedCastLifecycleKind.JUMP_START,
        FixedCastLifecycleKind.REBOUND,
    }:
        return None
    rebound = spec.kind is FixedCastLifecycleKind.REBOUND
    return ReplacementEffect(
        effect_id=fixed_cast_lifecycle_replacement_effect_id(
            spec.kind,
            card.logical_object_id,
        ),
        source_id=card.ref,
        event_kind="zone.change",
        replacement_class=(
            ReplacementClass.OTHER
            if rebound
            else ReplacementClass.SELF_REPLACEMENT
        ),
        conditions={
            "origin": {"eq": "stack"},
            "destination": (
                {"eq": "graveyard"}
                if rebound
                else {"not_in": ["exile"]}
            ),
            "object_ref": {"eq": card.ref},
            "logical_object_id": {"eq": card.logical_object_id},
            **(
                {
                    "transition_kind": {
                        "eq": ZoneTransitionKind.ORDINARY.value
                    }
                }
                if rebound
                else {}
            ),
        },
        operations=(SetField("destination", "exile"),),
        label=(
            f"{card.ref}: exile the resolving spell with Rebound"
            if rebound
            else f"{card.ref}: exile the jump-started spell instead"
        ),
    )


def fixed_cast_lifecycle_subject_replacements(
    cards: Mapping[str, CardInstance],
    object_ids: Sequence[str],
) -> tuple[ReplacementEffect, ...]:
    return tuple(
        replacement
        for object_id in object_ids
        if (
            replacement := fixed_cast_lifecycle_stack_replacement(
                cards.get(object_id)
            )
        )
        is not None
    )


def complete_fixed_cast_lifecycle_resolution(
    host: FixedCastLifecycleHost,
    *,
    item: StackItem,
    card: CardInstance,
    resolved_logical_object_id: str | None = None,
    applied_replacement_effect_ids: Sequence[str] = (),
) -> None:
    """Apply the chosen lifecycle only after a stack object resolves."""

    suspended = item.context.get(SUSPEND_HASTE_CONTEXT_FIELD)
    if isinstance(suspended, Mapping):
        spec = FixedCastLifecycleSpec.from_dict(suspended)
        if spec.kind is not FixedCastLifecycleKind.SUSPEND:
            raise FixedCastLifecycleError(
                "Suspend Haste context has the wrong lifecycle"
            )
        if card.zone == "battlefield" and card.object_kind == "card":
            normalized_zone_object_keyword("Haste")
            commit_zone_object_keyword_grant(
                host,
                card=card,
                source=ResolutionEffectSource(
                    stack_ref=item.ref,
                    object_id=card.object_id,
                    logical_object_id=card.logical_object_id,
                    card_ref=card.ref,
                ),
                keyword="Haste",
                duration=(
                    ContinuousEffectDuration.UNTIL_CONTROL_CHANGE
                ),
            )
        return
    raw = item.context.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if not isinstance(raw, Mapping):
        return
    spec = FixedCastLifecycleSpec.from_dict(raw)
    if spec.kind is FixedCastLifecycleKind.REBOUND:
        applied_rebound = bool(
            resolved_logical_object_id
            and fixed_cast_lifecycle_replacement_effect_id(
                spec.kind,
                resolved_logical_object_id,
            )
            in applied_replacement_effect_ids
        )
        if (
            item.context.get("rebound_from_hand") is True
            and applied_rebound
            and card.zone == "exile"
            and card.object_kind == "card"
        ):
            schedule_delayed_trigger(
                host,
                controller=item.controller,
                label=f"{card.printed_name} — cast from Rebound",
                event_kind="step.begin",
                condition={
                    "phase": "beginning",
                    "step": "upkeep",
                    "player": item.controller,
                },
                stack_template={
                    "label": f"{card.printed_name} — cast from Rebound",
                    "semantic_key": REBOUND_CAST_SEMANTIC_KEY,
                    "context": {
                        "source_logical_object_id": card.logical_object_id,
                        "rebound_spec": spec.to_dict(),
                    },
                },
                source_object_id=card.object_id,
                referred_object_ids=(card.object_id,),
                once=True,
            )
        return
    if spec.kind not in {
        FixedCastLifecycleKind.DASH,
        FixedCastLifecycleKind.WARP,
    }:
        return
    if card.zone != "battlefield" or card.object_kind != "card":
        return
    if spec.kind is FixedCastLifecycleKind.DASH:
        normalized_zone_object_keyword("Haste")
        commit_zone_object_keyword_grant(
            host,
            card=card,
            source=ResolutionEffectSource(
                stack_ref=item.ref,
                object_id=card.object_id,
                logical_object_id=card.logical_object_id,
                card_ref=card.ref,
            ),
            keyword="Haste",
        )
    destination = (
        "hand" if spec.kind is FixedCastLifecycleKind.DASH else "exile"
    )
    move_effect = {
        "op": "move_if_in_zone",
        "card": card.ref,
        "from": "battlefield",
        "destination": destination,
        "expected_zone_change_counter": card.zone_change_counter,
        "expected_object_identity": card.logical_object_id,
    }
    if spec.kind is FixedCastLifecycleKind.WARP:
        move_effect["then_cast_permission"] = TemporaryCastPermissionGrant(
            player=item.controller,
            duration=ImpulseAccessDuration.UNTIL_USED,
            not_before_turn_sequence=host.state.turn_sequence + 1,
            without_mana_cost=False,
            source=f"warp:{card.logical_object_id}",
        ).to_dict()
    schedule_delayed_trigger(
        host,
        controller=item.controller,
        label=(
            f"Return {card.ref} at the next end step"
            if destination == "hand"
            else f"Exile {card.ref} at the next end step"
        ),
        event_kind="step.begin",
        condition={"phase": "ending", "step": "end_step"},
        stack_template={
            "label": (
                f"{spec.kind.value.title()} — move {card.ref} to {destination}"
            ),
            "context": {"dynamic_effects": [move_effect]},
        },
        source_object_id=card.object_id,
        referred_object_ids=(card.object_id,),
        once=True,
    )


__all__ = [
    "compile_fixed_cast_lifecycle",
    "complete_fixed_cast_lifecycle_resolution",
    "FixedCastLifecycleError",
    "FixedCastLifecycleHost",
    "FixedCastLifecycleKind",
    "FixedCastLifecycleSpec",
    "FixedZoneCastDesignation",
    "escape_other_card_cost_descriptor",
    "fixed_cast_lifecycle_stack_replacement",
    "fixed_cast_lifecycle_subject_replacements",
    "fixed_cast_lifecycle_handler_descriptor",
    "fixed_cast_lifecycle_resolution_destination",
    "fixed_cast_lifecycle_stack_fields",
    "fixed_cast_lifecycle_replacement_effect_id",
    "FIXED_CAST_LIFECYCLE_CAPABILITY_ID",
    "FIXED_ZONE_CAST_LIFECYCLE_CAPABILITY_ID",
    "FIXED_CAST_LIFECYCLE_CONTEXT_FIELD",
    "FIXED_CAST_LIFECYCLE_HANDLER_ID",
    "FIXED_CAST_LIFECYCLE_RUNTIME_EVENT",
    "FIXED_CAST_LIFECYCLE_STACK_ANNOTATION",
    "FIXED_ZONE_CAST_DESIGNATION_FIELD",
    "fixed_zone_cast_designation",
    "fixed_zone_cast_timing_is_legal",
    "jump_start_discard_cost_descriptor",
    "REBOUND_CAST_SEMANTIC_KEY",
    "REBOUND_EXILE_CAST_PRODUCER",
    "SUSPEND_HASTE_CONTEXT_FIELD",
    "retrace_land_discard_cost_descriptor",
]
