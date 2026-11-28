from __future__ import annotations

"""Typed fixed public alternative costs and their current eligibility."""

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Protocol, Sequence

from .additional_cost_vocabulary import EXILE_ONE_FROM_HAND_COST
from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .damage_turn_history import (
    COMBAT_DAMAGE_HISTORY_MARKER,
    COMMANDER_DAMAGE_HISTORY_MARKER,
)
from .live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from .object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from .replacement.immutable import FrozenMap, thaw_value
from .rules.casting_additional_cost_groups import (
    FixedLifePaymentAdditionalCost,
    fixed_life_payment_additional_cost,
)
from .rules.casting_additional_costs import (
    AdditionalCostError,
    FixedZoneChangeAdditionalCost,
    fixed_zone_change_additional_cost,
)
from .util import mana_cost_to_vector, stable_json


FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY = (
    "casting.alternative_cost.fixed_public"
)
FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID = (
    "casting.alternative-cost.fixed-public.v1"
)
FIXED_PUBLIC_ALTERNATIVE_COST_EVENT = "cast.cost"
ANNOUNCED_X_PITCH_PRICE_VERSION = 2
LIFE_AND_BLUE_HAND_EXILE_PRICE_VERSION = 3
LIFE_AND_BLUE_HAND_EXILE_PRICE_CAPABILITIES = (
    "casting.additional_cost.zone_change.fixed_exile",
    "casting.additional_cost.fixed_life_payment",
)
ANNOUNCED_X_PITCH_POLICY = "announced_x_pitch"
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
    LAND_PAIR = 'land_pair'
    CONTROLLED_COMMANDER = 'controlled_commander'
    ALWAYS = "always"
    CONTROLLER_CAST_ANOTHER_SPELL = "controller_cast_another_spell"
    CONTROLS_BASIC_LAND_TYPE = "controls_basic_land_type"
    FREERUNNING = "freerunning"
    NOT_YOUR_TURN = "not_your_turn"
    OPPONENT_LOST_LIFE = "opponent_lost_life"
    OPPONENT_GAINED_LIFE = "opponent_gained_life"
    OPPONENT_DREW_THREE = "opponent_drew_three"
    OPPONENT_BLUE_CAST = "opponent_blue_cast"
    OPPONENT_CAST_THREE_SPELLS = "opponent_cast_three_spells"
    OPPONENT_COUNTERED_CREATURE_CAST = "opponent_countered_creature_cast"
    OPPONENT_GRAVEYARD_ARRIVALS = "opponent_graveyard_arrivals"
    OPPONENT_CREATURE_ARRIVALS = "opponent_creature_arrivals"
    OPPONENT_LAND_ARRIVALS = "opponent_land_arrivals"
    EXACTLY_ONE_ATTACKING_CREATURE = "exactly_one_attacking_creature"
    AT_LEAST_THREE_ATTACKING_CREATURES = "at_least_three_attacking_creatures"
    AT_LEAST_FOUR_ATTACKING_CREATURES = "at_least_four_attacking_creatures"
    MINIMUM_BATTLEFIELD_CREATURES = "minimum_battlefield_creatures"
    DAMAGED_BY_TWO_CREATURES = "damaged_by_two_creatures"
    PROWL = "prowl"
    YOUR_TURN = "your_turn"


LIVE_ATTACKING_PRICE_VERSION = 5
LIVE_ATTACKING_COSTS = {
    FixedPublicAlternativeConditionKind.EXACTLY_ONE_ATTACKING_CREATURE: "{W}",
    FixedPublicAlternativeConditionKind.AT_LEAST_THREE_ATTACKING_CREATURES: "{U}",
    FixedPublicAlternativeConditionKind.AT_LEAST_FOUR_ATTACKING_CREATURES: "{1}{W}",
}
_LIVE_ATTACKING_MINIMUM_COUNTS = {
    FixedPublicAlternativeConditionKind.AT_LEAST_THREE_ATTACKING_CREATURES: 3,
    FixedPublicAlternativeConditionKind.AT_LEAST_FOUR_ATTACKING_CREATURES: 4,
}
CREATURE_DAMAGE_PRICE_VERSION = 6
BATTLEFIELD_CREATURE_PRICE_VERSION = 7
_BATTLEFIELD_CREATURE_COUNT = LiveBattlefieldQuantitySpec(
    "any", ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
)
_CREATURE_DAMAGE_COSTS = {FixedPublicAlternativeConditionKind.DAMAGED_BY_TWO_CREATURES: "{R}"}
_CLOSED_PLAIN_PRICE_COSTS = {
    LIVE_ATTACKING_PRICE_VERSION: LIVE_ATTACKING_COSTS,
    CREATURE_DAMAGE_PRICE_VERSION: _CREATURE_DAMAGE_COSTS,
}
_ATTACKING_CREATURE_COUNT = LiveBattlefieldQuantitySpec(
    "any",
    ObjectQuerySpec(
        zones=("battlefield",), types_all=("creature",),
        state_predicate=PermanentStatePredicateSpec(attacking=True),
    ),
)


OPPONENT_HISTORY_PRICE_VERSION = 4
_LEGACY_OPPONENT_HISTORY_PRICE_VERSION = 3
_OPPONENT_HISTORY_PRICE_VERSIONS = (_LEGACY_OPPONENT_HISTORY_PRICE_VERSION, OPPONENT_HISTORY_PRICE_VERSION)
OPPONENT_HISTORY_COSTS = {
    FixedPublicAlternativeConditionKind.OPPONENT_DREW_THREE: "{R}",
    FixedPublicAlternativeConditionKind.OPPONENT_BLUE_CAST: "{R}",
    FixedPublicAlternativeConditionKind.OPPONENT_CAST_THREE_SPELLS: "{0}",
    FixedPublicAlternativeConditionKind.OPPONENT_COUNTERED_CREATURE_CAST: "{0}",
    FixedPublicAlternativeConditionKind.OPPONENT_GRAVEYARD_ARRIVALS: "{0}",
    FixedPublicAlternativeConditionKind.OPPONENT_CREATURE_ARRIVALS: "{U}",
    FixedPublicAlternativeConditionKind.OPPONENT_GAINED_LIFE: "{B}",
    FixedPublicAlternativeConditionKind.OPPONENT_LAND_ARRIVALS: "{3}{R}{R}",
}
_V4_ONLY_OPPONENT_HISTORY_KINDS = frozenset({
    FixedPublicAlternativeConditionKind.OPPONENT_GAINED_LIFE,
    FixedPublicAlternativeConditionKind.OPPONENT_LAND_ARRIVALS,
    FixedPublicAlternativeConditionKind.OPPONENT_CAST_THREE_SPELLS,
})
_OPPONENT_HISTORY_COUNTS = {
    FixedPublicAlternativeConditionKind.OPPONENT_CAST_THREE_SPELLS: ("spell_cast", (), 3),
    FixedPublicAlternativeConditionKind.OPPONENT_GRAVEYARD_ARRIVALS: ("card_arrived_graveyard", (), 3),
    FixedPublicAlternativeConditionKind.OPPONENT_CREATURE_ARRIVALS: ("permanent_entered", ("creature",), 2),
    FixedPublicAlternativeConditionKind.OPPONENT_LAND_ARRIVALS: ("permanent_entered", ("land",), 2),
}


@dataclass(frozen=True, slots=True)
class FixedPublicAlternativeCondition:
    kind: FixedPublicAlternativeConditionKind
    basic_land_type: str | None = None
    schema_version: int = 1
    opponent_basic_land_type: str | None = None
    minimum_creature_count: int | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is int and self.schema_version == BATTLEFIELD_CREATURE_PRICE_VERSION:
            if (self.kind is not FixedPublicAlternativeConditionKind.MINIMUM_BATTLEFIELD_CREATURES
                    or type(self.minimum_creature_count) is not int or not 1 <= self.minimum_creature_count <= 99
                    or self.basic_land_type is not None or self.opponent_basic_land_type is not None):
                raise FixedPublicAlternativeCostError("The battlefield price requires its exact creature threshold")
            return
        if (self.minimum_creature_count is not None
                or self.kind is FixedPublicAlternativeConditionKind.MINIMUM_BATTLEFIELD_CREATURES):
            raise FixedPublicAlternativeCostError("A creature threshold requires its explicit condition version")
        if type(self.schema_version) is int and self.schema_version in _OPPONENT_HISTORY_PRICE_VERSIONS:
            if (not isinstance(self.kind, FixedPublicAlternativeConditionKind)
                    or self.kind not in OPPONENT_HISTORY_COSTS
                    or self.basic_land_type is not None or self.opponent_basic_land_type is not None
                    or (self.schema_version == _LEGACY_OPPONENT_HISTORY_PRICE_VERSION
                        and self.kind in _V4_ONLY_OPPONENT_HISTORY_KINDS)):
                raise FixedPublicAlternativeCostError("Opponent history requires its exact fact and version")
            return
        if type(self.schema_version) is int and self.schema_version in _CLOSED_PLAIN_PRICE_COSTS:
            if (not isinstance(self.kind, FixedPublicAlternativeConditionKind)
                    or self.kind not in _CLOSED_PLAIN_PRICE_COSTS[self.schema_version]
                    or self.basic_land_type is not None or self.opponent_basic_land_type is not None):
                raise FixedPublicAlternativeCostError("Fixed-count prices require their exact condition")
            return
        if self.schema_version == 2:
            from .conditional_free_cast_model import validate_free_cast_condition
            if not isinstance(self.kind, FixedPublicAlternativeConditionKind):
                raise FixedPublicAlternativeCostError('Alternative-cost condition kind is unsupported')
            try:
                validate_free_cast_condition(kind=self.kind.value, own_land=self.basic_land_type,
                    opponent_land=self.opponent_basic_land_type, schema_version=self.schema_version)
            except ValueError as exc:
                raise FixedPublicAlternativeCostError(str(exc)) from exc
            return
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedPublicAlternativeCostError(
                "Unsupported alternative-cost condition version"
            )
        if not isinstance(self.kind, FixedPublicAlternativeConditionKind):
            raise FixedPublicAlternativeCostError(
                "Alternative-cost condition kind is unsupported"
            )
        if self.kind in OPPONENT_HISTORY_COSTS:
            raise FixedPublicAlternativeCostError("Opponent history prices require their explicit version")
        if self.kind in LIVE_ATTACKING_COSTS or self.kind in _CREATURE_DAMAGE_COSTS:
            raise FixedPublicAlternativeCostError("Fixed-count prices require their explicit version")
        if self.opponent_basic_land_type is not None or self.kind in {
                FixedPublicAlternativeConditionKind.LAND_PAIR, FixedPublicAlternativeConditionKind.CONTROLLED_COMMANDER}:
            raise FixedPublicAlternativeCostError('Extended free-cast conditions require version2')
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

    @property
    def history_dependencies(self) -> tuple[str, ...]:
        if (self.kind in _CREATURE_DAMAGE_COSTS
                or self.kind in OPPONENT_HISTORY_COSTS
                and self.kind is not FixedPublicAlternativeConditionKind.OPPONENT_DREW_THREE):
            from .public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY

            return (PUBLIC_HISTORY_QUERY_CAPABILITY,)
        return ()

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        if self.schema_version == LIVE_ATTACKING_PRICE_VERSION:
            return _ATTACKING_CREATURE_COUNT.capability_dependencies
        if self.schema_version == BATTLEFIELD_CREATURE_PRICE_VERSION:
            return _BATTLEFIELD_CREATURE_COUNT.capability_dependencies
        return self.history_dependencies

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "basic_land_type": self.basic_land_type,
        }
        if self.schema_version == 2:
            value['opponent_basic_land_type'] = self.opponent_basic_land_type
        if self.schema_version == BATTLEFIELD_CREATURE_PRICE_VERSION:
            value["minimum_creature_count"] = self.minimum_creature_count
        return value

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FixedPublicAlternativeCondition":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "kind",
            "basic_land_type",
            *({'opponent_basic_land_type'} if value.get('schema_version') == 2 else ()),
            *({"minimum_creature_count"} if value.get("schema_version") == BATTLEFIELD_CREATURE_PRICE_VERSION else ()),
        }:
            raise FixedPublicAlternativeCostError(
                "Alternative-cost condition fields are closed"
            )
        try:
            return cls(
                schema_version=value["schema_version"],
                kind=FixedPublicAlternativeConditionKind(value["kind"]),
                basic_land_type=value["basic_land_type"],
                opponent_basic_land_type=value.get('opponent_basic_land_type'),
                minimum_creature_count=value.get("minimum_creature_count"),
            )
        except (TypeError, ValueError) as exc:
            raise FixedPublicAlternativeCostError(str(exc)) from exc


def life_and_blue_hand_exile_payments() -> tuple[FrozenMap, ...]:
    life = FixedLifePaymentAdditionalCost(amount=1)
    exile = FixedZoneChangeAdditionalCost(
        operation=EXILE_ONE_FROM_HAND_COST,
        count=1,
        choice_field="exile_cards",
        predicate=ObjectQuerySpec(
            zones=("hand",), owner="$actor", known_to_actor=True, colors_all=("U",),
        ),
    )
    return (FrozenMap(life.to_descriptor()), FrozenMap(exile.to_descriptor()))


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
        if type(self.schema_version) is not int or self.schema_version not in (
            1, ANNOUNCED_X_PITCH_PRICE_VERSION, LIFE_AND_BLUE_HAND_EXILE_PRICE_VERSION,
        ):
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
        requirements = self._validated_mana_price()
        if self.schema_version == LIFE_AND_BLUE_HAND_EXILE_PRICE_VERSION:
            if (
                self.kind is not FixedPublicAlternativeCostKind.PLAIN
                or self.condition != FixedPublicAlternativeCondition(FixedPublicAlternativeConditionKind.ALWAYS)
                or self.cost_text is not None
                or self.additional_costs != life_and_blue_hand_exile_payments()
            ):
                raise FixedPublicAlternativeCostError(
                    "This alternative requires both one life and one blue card exiled from the caster's hand"
                )
        elif len(self.additional_costs) > 1:
            raise FixedPublicAlternativeCostError(
                "Fixed alternative costs support at most one nonmana payment"
            )
        if self.schema_version == ANNOUNCED_X_PITCH_PRICE_VERSION and (
            self.kind is not FixedPublicAlternativeCostKind.PLAIN
            or self.condition != FixedPublicAlternativeCondition(FixedPublicAlternativeConditionKind.ALWAYS)
            or self.cost_text is not None or len(self.additional_costs) != 1
        ):
            raise FixedPublicAlternativeCostError("Announced X requires exactly one hand-exile alternative")
        for raw in self.additional_costs:
            descriptor = thaw_value(raw)
            try:
                zone = fixed_zone_change_additional_cost(descriptor)
                life = fixed_life_payment_additional_cost(descriptor)
            except (AdditionalCostError, TypeError) as exc:
                raise FixedPublicAlternativeCostError(str(exc)) from exc
            if (zone is None) is (life is None) or (zone is not None and zone.count_parameter is not None):
                raise FixedPublicAlternativeCostError(
                    "Fixed alternative-cost payment is unsupported"
                )
            if self.schema_version == ANNOUNCED_X_PITCH_PRICE_VERSION:
                colors = zone.predicate.colors_all if zone is not None else ()
                if (zone is None or zone.schema_version != 1 or zone.operation != EXILE_ONE_FROM_HAND_COST
                        or zone.count != 1 or len(colors) != 1 or colors[0] not in "WUBRG"
                        or zone.predicate != ObjectQuerySpec(
                            zones=("hand",), owner="$actor", known_to_actor=True, colors_all=colors,
                        )):
                    raise FixedPublicAlternativeCostError("Announced X requires one card of its stated hand color")
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
            FixedPublicAlternativeConditionKind.LAND_PAIR,
            FixedPublicAlternativeConditionKind.CONTROLLED_COMMANDER,
            FixedPublicAlternativeConditionKind.ALWAYS,
            FixedPublicAlternativeConditionKind.CONTROLS_BASIC_LAND_TYPE,
            FixedPublicAlternativeConditionKind.NOT_YOUR_TURN,
            FixedPublicAlternativeConditionKind.YOUR_TURN,
            FixedPublicAlternativeConditionKind.MINIMUM_BATTLEFIELD_CREATURES,
            *OPPONENT_HISTORY_COSTS,
            *LIVE_ATTACKING_COSTS,
            *_CREATURE_DAMAGE_COSTS,
        }:
            raise FixedPublicAlternativeCostError(
                "Plain alternative cost has an unsupported condition"
            )
        if self.condition.schema_version == 2 and (
                self.kind is not FixedPublicAlternativeCostKind.PLAIN or self.cost_text != '{0}'
                or any(requirements.values()) or self.additional_costs):
            raise FixedPublicAlternativeCostError('Conditional zero-mana alternative requires exactly the zero replacement price')
        if self.condition.schema_version == BATTLEFIELD_CREATURE_PRICE_VERSION and (
                self.kind is not FixedPublicAlternativeCostKind.PLAIN
                or self.cost_text is None or self.additional_costs):
            raise FixedPublicAlternativeCostError("A battlefield creature price requires ordinary mana only")
        closed_prices = _CLOSED_PLAIN_PRICE_COSTS.get(self.condition.schema_version)
        if closed_prices is not None and (
            self.kind is not FixedPublicAlternativeCostKind.PLAIN
            or self.cost_text != closed_prices[self.condition.kind]
            or self.additional_costs
        ):
            raise FixedPublicAlternativeCostError("Fixed-count prices require the exact ordinary mana alternative")
        if self.condition.schema_version in _OPPONENT_HISTORY_PRICE_VERSIONS and (
            self.kind is not FixedPublicAlternativeCostKind.PLAIN
            or self.cost_text != OPPONENT_HISTORY_COSTS[self.condition.kind]
            or self.additional_costs
        ):
            raise FixedPublicAlternativeCostError("Opponent history requires its exact ordinary mana alternative")

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

    def _validated_mana_price(self) -> dict[str, int]:
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
        return requirements

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
            **({"x_value_policy": ANNOUNCED_X_PITCH_POLICY}
               if self.schema_version == ANNOUNCED_X_PITCH_PRICE_VERSION else {}),
        }


def is_life_and_blue_hand_exile_price_option(
    option: Mapping[str, Any], costs: Sequence[Mapping[str, Any]],
) -> bool:
    body = option.get("fixed_public_alternative_cost")
    if (
        not isinstance(body, Mapping) or body.get("schema_version") != LIFE_AND_BLUE_HAND_EXILE_PRICE_VERSION
        or len(costs) != 2 or any(not isinstance(cost, Mapping) for cost in costs)
    ):
        return False
    try:
        spec = FixedPublicAlternativeCostSpec.from_dict(thaw_value(FrozenMap(body)))
        actual = [thaw_value(FrozenMap(cost)) for cost in costs]
        if stable_json(actual) != stable_json(spec.to_dict()["additional_costs"]):
            return False
    except (TypeError, ValueError):
        return False
    return option.get("kind") == "alternate" and option.get("id") == spec.option_id


def life_and_blue_hand_exile_price_payload_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    if value.get("handler_id") != FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID:
        return ()
    body = value.get("alternative_cost")
    if not isinstance(body, Mapping) or body.get("schema_version") != LIFE_AND_BLUE_HAND_EXILE_PRICE_VERSION:
        return ()
    from .semantic_runtime.public_alternative_costs import FixedPublicAlternativeCostHandler

    if type(value.get("schema_version")) is not int:
        raise FixedPublicAlternativeCostError("The fixed alternative handler requires an exact schema version")
    FixedPublicAlternativeCostHandler().validate(thaw_value(FrozenMap(value)))
    return LIFE_AND_BLUE_HAND_EXILE_PRICE_CAPABILITIES


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


def opponent_history_price_payload_dependencies(value):
    if value.get("handler_id") != FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID:
        return ()
    price = value.get("alternative_cost")
    condition = price.get("condition") if isinstance(price, Mapping) else None
    if not isinstance(condition, Mapping) or condition.get("schema_version") not in _OPPONENT_HISTORY_PRICE_VERSIONS:
        return ()
    return FixedPublicAlternativeCostSpec.from_dict(price).condition.history_dependencies


def live_attacking_price_payload_dependencies(value):
    if value.get("handler_id") != FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID:
        return ()
    price = value.get("alternative_cost")
    condition = price.get("condition") if isinstance(price, Mapping) else None
    if not isinstance(condition, Mapping) or condition.get("schema_version") != LIVE_ATTACKING_PRICE_VERSION:
        return ()
    return FixedPublicAlternativeCostSpec.from_dict(price).condition.capability_dependencies


def battlefield_creature_price_payload_dependencies(value):
    if value.get("handler_id") != FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID:
        return ()
    price = value.get("alternative_cost")
    condition = price.get("condition") if isinstance(price, Mapping) else None
    if not isinstance(condition, Mapping) or condition.get("schema_version") != BATTLEFIELD_CREATURE_PRICE_VERSION:
        return ()
    return FixedPublicAlternativeCostSpec.from_dict(price).condition.capability_dependencies


def creature_damage_price_payload_dependencies(value):
    if value.get("handler_id") != FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID:
        return ()
    price = value.get("alternative_cost")
    condition = price.get("condition") if isinstance(price, Mapping) else None
    if not isinstance(condition, Mapping) or condition.get("schema_version") != CREATURE_DAMAGE_PRICE_VERSION:
        return ()
    return FixedPublicAlternativeCostSpec.from_dict(price).condition.history_dependencies


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


def _opponent_history_price_met(host, seat, condition):
    kind = condition.kind
    # CR 800.4i keeps past actions after a player leaves. The recorded
    # version-three condition retains its earlier live-opponent domain.
    if condition.schema_version == _LEGACY_OPPONENT_HISTORY_PRICE_VERSION:
        seats = host.active_seats
    else:
        seats = sorted(host.state.players)
    opponents = tuple(player for player in seats if player != seat)
    if kind is FixedPublicAlternativeConditionKind.OPPONENT_DREW_THREE:
        from .drawing.restrictions import drawn_this_turn

        return any(drawn_this_turn(host, opponent) >= 3 for opponent in opponents)
    from .public_history_query_model import PublicHistoryQuerySpec, matching_public_history_events

    if kind is FixedPublicAlternativeConditionKind.OPPONENT_BLUE_CAST:
        from .public_history_query_model import current_opponent_spell_colors

        colors = current_opponent_spell_colors(
            host.state, seat, include_departed=condition.schema_version != _LEGACY_OPPONENT_HISTORY_PRICE_VERSION,
        )
        return colors is not None and "U" in colors

    if kind is FixedPublicAlternativeConditionKind.OPPONENT_COUNTERED_CREATURE_CAST:
        from .turn_history_model import CAST_CREATURE_COUNTERED_EVENT

        spec = PublicHistoryQuerySpec(event=CAST_CREATURE_COUNTERED_EVENT, relation="controller")
        occurrences = matching_public_history_events(host.state, seat, spec)
        return any(fact.target in opponents for fact in occurrences or ())

    if kind is FixedPublicAlternativeConditionKind.OPPONENT_GAINED_LIFE:
        spec = PublicHistoryQuerySpec(event="player_gained_life", relation="any_player", reduction="sum_amount")
        occurrences = matching_public_history_events(host.state, seat, spec)
        return any(fact.target in opponents and fact.amount > 0 for fact in occurrences or ())

    event, types, threshold = _OPPONENT_HISTORY_COUNTS[kind]
    spec = PublicHistoryQuerySpec(event=event, relation="any_player", types_all=types)
    occurrences = matching_public_history_events(host.state, seat, spec)
    counts = Counter(fact.actor for fact in occurrences or () if fact.actor in opponents)
    return any(count >= threshold for count in counts.values())


def _creature_damage_price_met(host, seat):
    from .public_history_query_model import PublicHistoryQuerySpec, matching_public_history_events

    query = PublicHistoryQuerySpec(event="player_damaged", relation="controller", types_all=("creature",))
    occurrences = matching_public_history_events(host.state, seat, query)
    sources = [fact.object_incarnation for fact in occurrences or ()
               if type(fact.amount) is int and fact.amount > 0 and fact.target_kind == "player"]
    if any(type(source) is not str or not source for source in sources):
        return False
    return len(set(sources)) >= 2


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
    if condition.schema_version in _OPPONENT_HISTORY_PRICE_VERSIONS:
        return _opponent_history_price_met(host, seat, condition)
    if condition.schema_version == CREATURE_DAMAGE_PRICE_VERSION:
        return _creature_damage_price_met(host, seat)
    if condition.schema_version == BATTLEFIELD_CREATURE_PRICE_VERSION:
        from .live_battlefield_quantities import current_live_battlefield_count

        assert condition.minimum_creature_count is not None
        return current_live_battlefield_count(host, seat, _BATTLEFIELD_CREATURE_COUNT) >= condition.minimum_creature_count
    if condition.schema_version == LIVE_ATTACKING_PRICE_VERSION:
        from .live_battlefield_quantities import current_live_battlefield_count

        count = current_live_battlefield_count(host, seat, _ATTACKING_CREATURE_COUNT)
        if kind is FixedPublicAlternativeConditionKind.EXACTLY_ONE_ATTACKING_CREATURE:
            return count == 1
        return count >= _LIVE_ATTACKING_MINIMUM_COUNTS[kind]
    if kind is FixedPublicAlternativeConditionKind.CONTROLLED_COMMANDER:
        return any(candidate.zone == 'battlefield' and not candidate.phased_out
            and candidate.controller == seat and candidate.is_commander for candidate in host.state.cards.values())
    if kind is FixedPublicAlternativeConditionKind.LAND_PAIR:
        return _controlled_basic_land(host, seat, condition.basic_land_type) and any(
            opponent != seat and _controlled_basic_land(host, opponent, condition.opponent_basic_land_type)
            for opponent in host.active_seats)
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
