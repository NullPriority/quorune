from __future__ import annotations

"""Typed fixed public alternative costs and their current eligibility."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Protocol, Sequence

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .damage_turn_history import (
    COMBAT_DAMAGE_HISTORY_MARKER,
    COMMANDER_DAMAGE_HISTORY_MARKER,
)
from .replacement.immutable import FrozenMap, thaw_value
from .rules.casting_additional_cost_groups import (
    fixed_life_payment_additional_cost,
)
from .rules.casting_additional_costs import (
    AdditionalCostError,
    fixed_zone_change_additional_cost,
)
from .util import mana_cost_to_vector


FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY = (
    "casting.alternative_cost.fixed_public"
)
FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID = (
    "casting.alternative-cost.fixed-public.v1"
)
FIXED_PUBLIC_ALTERNATIVE_COST_EVENT = "cast.cost"
FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC = "fixed-public-alternative-cost"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_ORDINARY_MANA = re.compile(r"(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+")
_BASIC_LAND_TYPES = frozenset(
    {"forest", "island", "mountain", "plains", "swamp"}
)


class FixedPublicAlternativeCostError(ValueError):
    """A fixed public alternative-cost descriptor is malformed."""


class FixedPublicAlternativeCostKind(str, Enum):
    PLAIN = "plain"
    FREERUNNING = "freerunning"
    PROWL = "prowl"
    SPECTACLE = "spectacle"
    SURGE = "surge"


class FixedPublicAlternativeConditionKind(str, Enum):
    ALWAYS = "always"
    CONTROLLER_CAST_ANOTHER_SPELL = "controller_cast_another_spell"
    CONTROLS_BASIC_LAND_TYPE = "controls_basic_land_type"
    FREERUNNING = "freerunning"
    NOT_YOUR_TURN = "not_your_turn"
    OPPONENT_LOST_LIFE = "opponent_lost_life"
    PROWL = "prowl"
    YOUR_TURN = "your_turn"


@dataclass(frozen=True, slots=True)
class FixedPublicAlternativeCondition:
    kind: FixedPublicAlternativeConditionKind
    basic_land_type: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedPublicAlternativeCostError(
                "Unsupported alternative-cost condition version"
            )
        if not isinstance(self.kind, FixedPublicAlternativeConditionKind):
            raise FixedPublicAlternativeCostError(
                "Alternative-cost condition kind is unsupported"
            )
        expected_land = (
            self.kind
            is FixedPublicAlternativeConditionKind.CONTROLS_BASIC_LAND_TYPE
        )
        if expected_land:
            normalized = str(self.basic_land_type or "").casefold()
            if normalized not in _BASIC_LAND_TYPES:
                raise FixedPublicAlternativeCostError(
                    "Alternative-cost land condition is unsupported"
                )
            object.__setattr__(self, "basic_land_type", normalized)
        elif self.basic_land_type is not None:
            raise FixedPublicAlternativeCostError(
                "Only a land condition may carry a basic land type"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "basic_land_type": self.basic_land_type,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FixedPublicAlternativeCondition":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "kind",
            "basic_land_type",
        }:
            raise FixedPublicAlternativeCostError(
                "Alternative-cost condition fields are closed"
            )
        try:
            return cls(
                schema_version=value["schema_version"],
                kind=FixedPublicAlternativeConditionKind(value["kind"]),
                basic_land_type=value["basic_land_type"],
            )
        except (TypeError, ValueError) as exc:
            raise FixedPublicAlternativeCostError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedPublicAlternativeCostSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    kind: FixedPublicAlternativeCostKind
    condition: FixedPublicAlternativeCondition
    cost_text: str | None
    mana_requirements: tuple[tuple[str, int], ...]
    additional_costs: tuple[FrozenMap, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedPublicAlternativeCostError(
                "Unsupported fixed alternative-cost version"
            )
        if (
            _ABILITY_ID.fullmatch(self.ability_id) is None
            or type(self.line_index) is not int
            or self.line_index < 0
            or self.ability_id != f"ab{self.line_index + 1}"
            or type(self.oracle_line) is not str
            or not self.oracle_line.strip()
        ):
            raise FixedPublicAlternativeCostError(
                "Fixed alternative cost requires source identity and text"
            )
        if not isinstance(self.kind, FixedPublicAlternativeCostKind):
            raise FixedPublicAlternativeCostError(
                "Fixed alternative-cost kind is unsupported"
            )
        if not isinstance(self.condition, FixedPublicAlternativeCondition):
            raise FixedPublicAlternativeCostError(
                "Fixed alternative cost requires a typed condition"
            )
        requirements = dict(self.mana_requirements)
        if (
            tuple(key for key, _ in self.mana_requirements) != _MANA_KEYS
            or len(requirements) != len(_MANA_KEYS)
            or any(
                type(amount) is not int or amount < 0
                for amount in requirements.values()
            )
        ):
            raise FixedPublicAlternativeCostError(
                "Fixed alternative-cost mana vector is malformed"
            )
        if self.cost_text is not None:
            normalized_cost = str(self.cost_text).upper()
            parsed, complex_symbols = mana_cost_to_vector(normalized_cost)
            if (
                _ORDINARY_MANA.fullmatch(normalized_cost) is None
                or complex_symbols
                or parsed != requirements
            ):
                raise FixedPublicAlternativeCostError(
                    "Fixed alternative-cost text does not match its mana"
                )
            object.__setattr__(self, "cost_text", normalized_cost)
        elif any(requirements.values()):
            raise FixedPublicAlternativeCostError(
                "Fixed alternative-cost mana requires source text"
            )
        if len(self.additional_costs) > 1:
            raise FixedPublicAlternativeCostError(
                "Fixed alternative costs support at most one nonmana payment"
            )
        for raw in self.additional_costs:
            descriptor = thaw_value(raw)
            try:
                zone = fixed_zone_change_additional_cost(descriptor)
                life = fixed_life_payment_additional_cost(descriptor)
            except (AdditionalCostError, TypeError) as exc:
                raise FixedPublicAlternativeCostError(str(exc)) from exc
            if (zone is None) is (life is None):
                raise FixedPublicAlternativeCostError(
                    "Fixed alternative-cost payment is unsupported"
                )
        if self.cost_text is None and not self.additional_costs:
            raise FixedPublicAlternativeCostError(
                "Fixed alternative cost cannot be empty"
            )
        expected_condition = {
            FixedPublicAlternativeCostKind.FREERUNNING: (
                FixedPublicAlternativeConditionKind.FREERUNNING
            ),
            FixedPublicAlternativeCostKind.PROWL: (
                FixedPublicAlternativeConditionKind.PROWL
            ),
            FixedPublicAlternativeCostKind.SPECTACLE: (
                FixedPublicAlternativeConditionKind.OPPONENT_LOST_LIFE
            ),
            FixedPublicAlternativeCostKind.SURGE: (
                FixedPublicAlternativeConditionKind.CONTROLLER_CAST_ANOTHER_SPELL
            ),
        }.get(self.kind)
        if expected_condition is not None and self.condition.kind is not expected_condition:
            raise FixedPublicAlternativeCostError(
                "Keyword alternative cost has the wrong condition"
            )
        if self.kind is FixedPublicAlternativeCostKind.PLAIN and self.condition.kind not in {
            FixedPublicAlternativeConditionKind.ALWAYS,
            FixedPublicAlternativeConditionKind.CONTROLS_BASIC_LAND_TYPE,
            FixedPublicAlternativeConditionKind.NOT_YOUR_TURN,
            FixedPublicAlternativeConditionKind.YOUR_TURN,
        }:
            raise FixedPublicAlternativeCostError(
                "Plain alternative cost has an unsupported condition"
            )

    @property
    def option_id(self) -> str:
        return f"{self.kind.value}-alternative-{self.ability_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "kind": self.kind.value,
            "condition": self.condition.to_dict(),
            "cost_text": self.cost_text,
            "mana_requirements": dict(self.mana_requirements),
            "additional_costs": [
                thaw_value(value) for value in self.additional_costs
            ],
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FixedPublicAlternativeCostSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "kind",
            "condition",
            "cost_text",
            "mana_requirements",
            "additional_costs",
        }:
            raise FixedPublicAlternativeCostError(
                "Fixed alternative-cost fields are closed"
            )
        raw_mana = value["mana_requirements"]
        raw_additional = value["additional_costs"]
        if (
            not isinstance(raw_mana, Mapping)
            or set(raw_mana) != set(_MANA_KEYS)
            or not isinstance(raw_additional, list)
        ):
            raise FixedPublicAlternativeCostError(
                "Fixed alternative-cost payload is malformed"
            )
        try:
            return cls(
                schema_version=value["schema_version"],
                ability_id=value["ability_id"],
                line_index=value["line_index"],
                oracle_line=value["oracle_line"],
                kind=FixedPublicAlternativeCostKind(value["kind"]),
                condition=FixedPublicAlternativeCondition.from_dict(
                    value["condition"]
                ),
                cost_text=value["cost_text"],
                mana_requirements=tuple(
                    (key, raw_mana[key]) for key in _MANA_KEYS
                ),
                additional_costs=tuple(
                    FrozenMap(item) for item in raw_additional
                ),
            )
        except (TypeError, ValueError) as exc:
            raise FixedPublicAlternativeCostError(str(exc)) from exc

    def cast_cost_option(self) -> dict[str, Any]:
        return {
            "id": self.option_id,
            "kind": "alternate",
            "label": f"Cast for {self.kind.value} alternative cost",
            "requirements": dict(self.mana_requirements),
            "condition": self.condition.to_dict(),
            "_additional_option_costs": [
                thaw_value(value) for value in self.additional_costs
            ],
            "fixed_public_alternative_cost": self.to_dict(),
        }


def fixed_public_alternative_cost_handler_descriptor(
    spec: FixedPublicAlternativeCostSpec,
) -> dict[str, Any]:
    return {
        "handler_id": FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID,
        "schema_version": 1,
        "event": FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "alternative_cost": spec.to_dict(),
    }


class FixedPublicAlternativeCostHost(Protocol):
    state: Any
    active_seats: Sequence[str]

    def _current_turn_history(self, kind: str) -> Sequence[Any]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def _controlled_basic_land(
    host: FixedPublicAlternativeCostHost,
    seat: str,
    subtype: str,
) -> bool:
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != seat or card.phased_out:
            continue
        effective = host._effective_card_data(card)
        types, subtypes, _ = host._type_parts(
            str(effective.get("type_line") or "")
        )
        if "land" in types and subtype in subtypes:
            return True
    return False


def fixed_public_alternative_condition_met(
    host: FixedPublicAlternativeCostHost,
    seat: str,
    card: Any,
    raw_condition: Mapping[str, Any],
) -> bool:
    """Evaluate one closed public condition from authoritative state."""

    try:
        condition = FixedPublicAlternativeCondition.from_dict(raw_condition)
    except (FixedPublicAlternativeCostError, TypeError):
        return False
    kind = condition.kind
    if kind is FixedPublicAlternativeConditionKind.ALWAYS:
        return True
    if kind is FixedPublicAlternativeConditionKind.YOUR_TURN:
        return host.state.active_player == seat
    if kind is FixedPublicAlternativeConditionKind.NOT_YOUR_TURN:
        return host.state.active_player != seat
    if kind is FixedPublicAlternativeConditionKind.CONTROLS_BASIC_LAND_TYPE:
        assert condition.basic_land_type is not None
        return _controlled_basic_land(
            host, seat, condition.basic_land_type
        )
    if kind is FixedPublicAlternativeConditionKind.CONTROLLER_CAST_ANOTHER_SPELL:
        return any(
            event.actor == seat
            for event in host._current_turn_history("spell_cast")
        )
    opponents = set(host.active_seats) - {seat}
    if kind is FixedPublicAlternativeConditionKind.OPPONENT_LOST_LIFE:
        return any(
            event.target in opponents and event.amount > 0
            for event in host._current_turn_history("player_lost_life")
        )
    effective = host._effective_card_data(card)
    card_types, card_subtypes, _ = host._type_parts(
        str(effective.get("type_line") or "")
    )
    for event in host._current_turn_history("player_damaged"):
        history_types = set(event.types)
        if (
            event.actor != seat
            or event.target not in opponents
            or event.amount <= 0
            or COMBAT_DAMAGE_HISTORY_MARKER not in history_types
        ):
            continue
        if kind is FixedPublicAlternativeConditionKind.PROWL:
            if card_subtypes.intersection(history_types):
                return True
        elif (
            kind is FixedPublicAlternativeConditionKind.FREERUNNING
            and "creature" in history_types
            and (
                "assassin" in history_types
                or COMMANDER_DAMAGE_HISTORY_MARKER in history_types
            )
        ):
            return True
    return False


__all__ = [
    "COMBAT_DAMAGE_HISTORY_MARKER",
    "COMMANDER_DAMAGE_HISTORY_MARKER",
    "FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY",
    "FIXED_PUBLIC_ALTERNATIVE_COST_EVENT",
    "FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID",
    "FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC",
    "FixedPublicAlternativeCondition",
    "FixedPublicAlternativeConditionKind",
    "FixedPublicAlternativeCostError",
    "FixedPublicAlternativeCostKind",
    "FixedPublicAlternativeCostSpec",
    "fixed_public_alternative_condition_met",
    "fixed_public_alternative_cost_handler_descriptor",
]
