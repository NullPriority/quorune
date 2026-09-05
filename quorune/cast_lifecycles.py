from __future__ import annotations

"""Typed fixed casting lifecycles and their post-resolution transitions."""

import copy
from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Mapping, Protocol, Sequence

from .additional_cost_vocabulary import DISCARD_ONE_COST
from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .continuous_effect_state import ResolutionEffectSource
from .impulse_access_model import (
    ImpulseAccessDuration,
    TemporaryCastPermissionGrant,
)
from .model import CardInstance, StackItem
from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value
from .rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from .trigger_processing import schedule_delayed_trigger
from .util import mana_cost_to_vector, stable_json
from .zone_object_keyword_grants import commit_zone_object_keyword_grant
from .zone_object_keyword_model import normalized_zone_object_keyword


FIXED_CAST_LIFECYCLE_CAPABILITY_ID = "casting.lifecycle.fixed_public"
FIXED_CAST_LIFECYCLE_HANDLER_ID = "casting.lifecycle.fixed-public.v1"
FIXED_CAST_LIFECYCLE_RUNTIME_EVENT = "cast.cost"
FIXED_CAST_LIFECYCLE_CONTEXT_FIELD = "fixed_cast_lifecycle"


class FixedCastLifecycleKind(str, Enum):
    BUYBACK = "buyback"
    DASH = "dash"
    MADNESS = "madness"
    WARP = "warp"
    RETRACE = "retrace"


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


class FixedCastLifecycleError(ValueError):
    """A fixed casting-lifecycle descriptor or transition is malformed."""


@dataclass(frozen=True, slots=True)
class FixedCastLifecycleSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    kind: FixedCastLifecycleKind
    cost_text: str | None
    mana_cost: FrozenMap | None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
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
        if self.kind is FixedCastLifecycleKind.RETRACE:
            if _RETRACE.fullmatch(self.oracle_line.strip()) is None:
                raise FixedCastLifecycleError(
                    "Retrace Oracle line is outside the closed grammar"
                )
            if self.cost_text is not None or self.mana_cost is not None:
                raise FixedCastLifecycleError(
                    "Retrace uses the printed cost and has no fixed mana field"
                )
            return
        match = _FIXED_LIFECYCLE.fullmatch(self.oracle_line.strip())
        if (
            match is None
            or match.group("mechanic").casefold() != self.kind.value
        ):
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle Oracle line does not match its kind"
            )
        if (
            type(self.cost_text) is not str
            or re.fullmatch(_ORDINARY_COST, self.cost_text) is None
            or match.group("cost").upper() != self.cost_text
        ):
            raise FixedCastLifecycleError(
                "Fixed cast lifecycle requires matching ordinary mana"
            )
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise FixedCastLifecycleError(
                    "Fixed cast-lifecycle mana cost must be an object"
                )
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        if (
            set(mana) != set(_MANA_FIELDS)
            or any(type(value) is not int or value < 0 for value in mana.values())
            or complex_symbols
            or mana != expected
        ):
            raise FixedCastLifecycleError(
                "Fixed cast-lifecycle mana vector does not match its cost"
            )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(stable_json(self.to_dict()).encode()).hexdigest()

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "FixedCastLifecycleSpec":
        expected = {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "kind",
            "cost_text",
            "mana_cost",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
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
            schema_version=value["schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
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

    def fixed_cost_option(self) -> dict[str, Any]:
        if self.kind is FixedCastLifecycleKind.RETRACE or self.mana_cost is None:
            raise FixedCastLifecycleError(
                "Retrace does not define a fixed-mana cost option"
            )
        option = {
            "id": self.kind.value,
            "kind": (
                "optional_additional"
                if self.kind is FixedCastLifecycleKind.BUYBACK
                else "alternate"
            ),
            "label": f"{self.kind.value.title()} {self.cost_text}",
            "requirements": thaw_value(self.mana_cost),
            FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: self.to_dict(),
            "fixed_cast_lifecycle_fingerprint": self.fingerprint,
        }
        if self.kind is FixedCastLifecycleKind.WARP:
            option["source_zone"] = "hand"
        if self.kind in {
            FixedCastLifecycleKind.DASH,
            FixedCastLifecycleKind.MADNESS,
            FixedCastLifecycleKind.WARP,
        }:
            option["x_value_policy"] = "zero"
        if self.kind is FixedCastLifecycleKind.MADNESS:
            option["source_zone"] = "exile"
        return option

    def retrace_cost_option(
        self,
        base_option: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.kind is not FixedCastLifecycleKind.RETRACE:
            raise FixedCastLifecycleError(
                "Only Retrace decorates a printed cost option"
            )
        option = copy.deepcopy(dict(base_option))
        base_id = str(option.get("id") or "normal")
        option.update(
            {
                "id": (
                    "retrace"
                    if base_id == "normal"
                    else f"retrace:{base_id}"
                ),
                "kind": "zone_cast",
                "label": "Retrace",
                "base_cost_option": base_id,
                FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: self.to_dict(),
                "fixed_cast_lifecycle_fingerprint": self.fingerprint,
                "source_zone": "graveyard",
                "_additional_option_costs": [
                    retrace_land_discard_cost_descriptor()
                ],
            }
        )
        return option


def compile_fixed_cast_lifecycle(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> FixedCastLifecycleSpec | None:
    """Compile a fixed-mana public lifecycle or ordinary Retrace."""

    normalized = " ".join(material_line.strip().split())
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
    """Apply Buyback only to a successful graveyard-bound resolution."""

    raw = item.context.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if not isinstance(raw, Mapping):
        return destination
    spec = FixedCastLifecycleSpec.from_dict(raw)
    if (
        spec.kind is FixedCastLifecycleKind.BUYBACK
        and destination in {None, "graveyard"}
    ):
        return "hand"
    return destination


def fixed_cast_lifecycle_stack_fields(
    selected_option: Mapping[str, Any],
    destination: str | None,
) -> tuple[str | None, dict[str, Any]]:
    """Return the initial destination and immutable stack lifecycle context."""

    raw = selected_option.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if not isinstance(raw, Mapping):
        return destination, {}
    spec = FixedCastLifecycleSpec.from_dict(raw)
    if (
        spec.kind is FixedCastLifecycleKind.BUYBACK
        and destination == "graveyard"
    ):
        destination = "hand"
    return destination, {
        FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: spec.to_dict()
    }


def complete_fixed_cast_lifecycle_resolution(
    host: FixedCastLifecycleHost,
    *,
    item: StackItem,
    card: CardInstance,
) -> None:
    """Apply the chosen lifecycle only after a permanent resolves."""

    raw = item.context.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if not isinstance(raw, Mapping):
        return
    spec = FixedCastLifecycleSpec.from_dict(raw)
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
    "fixed_cast_lifecycle_handler_descriptor",
    "fixed_cast_lifecycle_resolution_destination",
    "fixed_cast_lifecycle_stack_fields",
    "FIXED_CAST_LIFECYCLE_CAPABILITY_ID",
    "FIXED_CAST_LIFECYCLE_CONTEXT_FIELD",
    "FIXED_CAST_LIFECYCLE_HANDLER_ID",
    "FIXED_CAST_LIFECYCLE_RUNTIME_EVENT",
    "retrace_land_discard_cost_descriptor",
]
