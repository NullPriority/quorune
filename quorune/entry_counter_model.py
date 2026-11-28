from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Sequence

from .ability_fragment_primitives import INTRINSIC_ABILITIES_REMOVED
from .characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)


class EntryCounterError(ValueError):
    """An as-enters counter instruction is not representable."""


DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID = (
    "replacement.zone.dynamic-self-entry-counter.v1"
)
DYNAMIC_SELF_ENTRY_AMOUNTS_CONTEXT = "dynamic_self_entry_counter_amounts"
PAID_GREEN_PAIRS_ENTRY_CAPABILITY = "counter.producer.paid_green_pairs_self_entry"
PAID_COLOR_ENTRY_CAPABILITY = 'counter.producer.paid_color_self_entry'
CURRENT_LIFE_ENTRY_COUNTER_CAPABILITY = "counter.producer.current_life_self_entry"
COMMANDER_HISTORY_LOYALTY_ENTRY_CAPABILITY = "counter.producer.commander_cast_history_self_entry_loyalty"


class DynamicEntryCounterValueSource(str, Enum):
    CAST_X = "cast_x"
    MANA_COLORS_SPENT = "mana_colors_spent"
    MANA_OF_COLOR_SPENT = 'mana_of_color_spent'
    CONTROLLER_ATTACKED = "controller_attacked"
    CONTROLLER_OTHER_SPELLS_CAST = "controller_other_spells_cast"
    CONTROLLER_SPELLS_CAST = "controller_spells_cast"
    CONTROLLER_COMMANDER_CAST_COUNT = "controller_commander_cast_count"
    CONTROLLER_INSTANT_SORCERY_CASTS = "controller_instant_sorcery_casts"
    CREATURES_DIED = "creatures_died"
    OTHER_SPELLS_CAST = "other_spells_cast"
    OPPONENTS_LIFE_LOST = "opponents_life_lost"
    PUBLIC_QUERY = "public_query"
    CAST_FROM_HAND = "cast_from_hand"
    CAST_BY_ENTRY_CONTROLLER = "cast_by_entry_controller"
    ESCAPED = "escaped"
    MANA_WAS_SPENT = "mana_was_spent"
    CONTROLLER_PERMANENTS_LEFT = "controller_permanents_left"
    CONTROLLER_LIFE_GAINED = "controller_life_gained"
    CONTROLLER_LIFE_TOTAL = "controller_life_total"
    CONTROLLER_CREATURES_DIED = "controller_creatures_died"
    MAX_PLAYER_CREATURES = "max_player_creatures"
    MAX_EXILED_CARD_MANA_VALUE = "max_exiled_card_mana_value"
    MAX_OPPONENT_DRAWS = "max_opponent_draws"
    MAX_OTHER_CONTROLLED_POWER = "max_other_controlled_power"
    MAX_CONTROLLED_POWER_AND_GRAVEYARD = "max_controlled_power_and_graveyard"
    TOTAL_OTHER_CONTROLLED_TOUGHNESS = "total_other_controlled_toughness"
    TOTAL_OTHER_CONTROLLED_PLUS_COUNTERS = "total_other_controlled_plus_counters"
    OTHER_CONTROLLED_RED_AND_GREEN_COUNTS = "other_controlled_red_and_green_counts"


CAST_REVIVAL_ENTRY_CAPABILITY = "counter.producer.caster_revival_self_entry"
PUBLIC_MAXIMUM_ENTRY_COUNTER_CAPABILITY = "counter.producer.public_maximum_self_entry"
EXILED_MANA_VALUE_ENTRY_COUNTER_CAPABILITY = "counter.producer.exiled_mana_value_self_entry"
INSTANT_SORCERY_LOYALTY_ENTRY_CAPABILITY = "counter.producer.instant_sorcery_cast_loyalty_entry"
PUBLIC_MAXIMUM_ENTRY_COUNTER_NAMES = {
    DynamicEntryCounterValueSource.MAX_PLAYER_CREATURES: "suspect",
    DynamicEntryCounterValueSource.MAX_OPPONENT_DRAWS: "+1/+1",
}


ENTRY_CREATURE_VALUE_CAPABILITY = "counter.producer.current_creature_values_self_entry"
ENTRY_CREATURE_VALUE_SOURCES = frozenset({
    DynamicEntryCounterValueSource.MAX_OTHER_CONTROLLED_POWER,
    DynamicEntryCounterValueSource.MAX_CONTROLLED_POWER_AND_GRAVEYARD,
    DynamicEntryCounterValueSource.TOTAL_OTHER_CONTROLLED_TOUGHNESS,
    DynamicEntryCounterValueSource.TOTAL_OTHER_CONTROLLED_PLUS_COUNTERS,
    DynamicEntryCounterValueSource.OTHER_CONTROLLED_RED_AND_GREEN_COUNTS,
})


class DynamicEntryCounterCalculation(str, Enum):
    MULTIPLY = "multiply"
    PAID_GREEN_PAIRS = "paid_green_pairs"
    FIXED_IF_AT_LEAST = "fixed_if_at_least"
    FIXED_IF_BELOW = "fixed_if_below"


@dataclass(frozen=True, slots=True)
class DynamicEntryCounterAmountSpec:
    """One closed pre-entry amount calculation over public frozen facts."""

    value_source: DynamicEntryCounterValueSource
    calculation: DynamicEntryCounterCalculation
    coefficient: int = 1
    offset: int = 0
    minimum: int | None = None
    quantity: CharacteristicQuantitySpec | None = None
    mana_color: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.value_source, DynamicEntryCounterValueSource):
            try:
                object.__setattr__(
                    self,
                    "value_source",
                    DynamicEntryCounterValueSource(self.value_source),
                )
            except (TypeError, ValueError) as exc:
                raise EntryCounterError(
                    "Dynamic entry counter value source is unsupported"
                ) from exc
        if not isinstance(self.calculation, DynamicEntryCounterCalculation):
            try:
                object.__setattr__(
                    self,
                    "calculation",
                    DynamicEntryCounterCalculation(self.calculation),
                )
            except (TypeError, ValueError) as exc:
                raise EntryCounterError(
                    "Dynamic entry counter calculation is unsupported"
                ) from exc
        if type(self.coefficient) is not int or not 1 <= self.coefficient <= 10:
            raise EntryCounterError(
                "Dynamic entry counter coefficient must be from 1 through 10"
            )
        if self.value_source is DynamicEntryCounterValueSource.MANA_OF_COLOR_SPENT:
            if type(self.mana_color) is not str or self.mana_color not in tuple('WUBRG'):
                raise EntryCounterError('Paid-color entry amounts require one ordinary mana color')
        elif self.mana_color is not None:
            raise EntryCounterError('Only paid-color entry amounts accept a mana color')
        if type(self.offset) is not int or not 0 <= self.offset <= 10:
            raise EntryCounterError(
                "Dynamic entry counter offset must be from 0 through 10"
            )
        if self.calculation is DynamicEntryCounterCalculation.PAID_GREEN_PAIRS:
            if (self.value_source is not DynamicEntryCounterValueSource.MANA_OF_COLOR_SPENT
                    or self.mana_color != "G" or self.coefficient != 3 or self.offset or self.minimum is not None):
                raise EntryCounterError("Entry payment pairs require three counters per complete green pair")
        elif self.calculation is DynamicEntryCounterCalculation.MULTIPLY:
            if self.minimum is not None:
                raise EntryCounterError(
                    "Multiplying entry counter amounts take no minimum"
                )
        elif type(self.minimum) is not int or self.minimum < 1:
            raise EntryCounterError(
                "Conditional entry counter amounts require a positive minimum"
            )
        elif self.offset:
            raise EntryCounterError(
                "Conditional entry counter amounts cannot use an offset"
            )
        if self.value_source is DynamicEntryCounterValueSource.PUBLIC_QUERY:
            quantity = self.quantity
            if (
                not isinstance(quantity, CharacteristicQuantitySpec)
                or quantity.scope
                not in {
                    CharacteristicQuantityScope.CONTROLLER_ZONE,
                    CharacteristicQuantityScope.OPPONENT_ZONES,
                    CharacteristicQuantityScope.ALL_ZONES,
                }
                or quantity.query is None
                or (quantity.schema_version != 5 and quantity.query.zones
                    not in {("battlefield",), ("graveyard",)})
            ):
                raise EntryCounterError(
                    "Dynamic entry counter queries require the cycle-safe "
                    "public layer-5 boundary"
                )
        elif self.quantity is not None:
            raise EntryCounterError(
                "Only public-query entry counter amounts accept a quantity"
            )
        if self.value_source is DynamicEntryCounterValueSource.ESCAPED and (
            self.calculation is not DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST
            or self.minimum != 1
        ):
            raise EntryCounterError("Escaped entry counters require one fixed existence threshold")
        if self.value_source is DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT and (
            self.calculation is not DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST or self.minimum != 1
        ):
            raise EntryCounterError("Departure entry counters require one fixed existence threshold")
        if (self.value_source in PUBLIC_MAXIMUM_ENTRY_COUNTER_NAMES
                or self.value_source is DynamicEntryCounterValueSource.MAX_EXILED_CARD_MANA_VALUE) and (
            self.calculation is not DynamicEntryCounterCalculation.MULTIPLY
            or self.coefficient != 1 or self.offset != 0
        ):
            raise EntryCounterError("Maximum entry counts require one unmodified public maximum")
        if self.value_source in ENTRY_CREATURE_VALUE_SOURCES and (
            self.calculation is not DynamicEntryCounterCalculation.MULTIPLY
            or self.coefficient != 1 or self.offset != 0
        ):
            raise EntryCounterError("Entry creature values require one unmodified public aggregate")
        if self.value_source is DynamicEntryCounterValueSource.CONTROLLER_COMMANDER_CAST_COUNT and (
            self.calculation is not DynamicEntryCounterCalculation.MULTIPLY or self.coefficient != 1 or self.offset
        ):
            raise EntryCounterError("Commander history entry uses the unmodified lifetime command-zone cast count")
        if self.value_source in {
            DynamicEntryCounterValueSource.CONTROLLER_LIFE_TOTAL,
            DynamicEntryCounterValueSource.CONTROLLER_INSTANT_SORCERY_CASTS,
        } and (
            self.calculation is not DynamicEntryCounterCalculation.MULTIPLY
            or self.coefficient != 1
            or self.offset != 0
        ):
            raise EntryCounterError("Entry counters require the exact unscaled entering-controller fact")
        if self.value_source in {
            DynamicEntryCounterValueSource.CONTROLLER_LIFE_GAINED,
            DynamicEntryCounterValueSource.CONTROLLER_CREATURES_DIED,
        } and self.calculation is not DynamicEntryCounterCalculation.MULTIPLY:
            raise EntryCounterError("Life-gain and controller-death entry counters require multiplication")

    def amount(self, value: int) -> int:
        if type(value) is not int or value < 0:
            raise EntryCounterError(
                "Dynamic entry counter facts must be nonnegative integers"
            )
        if self.calculation is DynamicEntryCounterCalculation.MULTIPLY:
            return self.offset + self.coefficient * value
        if self.calculation is DynamicEntryCounterCalculation.PAID_GREEN_PAIRS:
            return self.coefficient * (value // 2)
        assert self.minimum is not None
        admitted = (
            value >= self.minimum
            if self.calculation
            is DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST
            else value < self.minimum
        )
        return self.coefficient if admitted else 0

    def to_dict(self) -> dict[str, Any]:
        value = {
            "value_source": self.value_source.value,
            "calculation": self.calculation.value,
            "coefficient": self.coefficient,
            "offset": self.offset,
            "minimum": self.minimum,
            "quantity": (
                self.quantity.to_dict() if self.quantity is not None else None
            ),
        }
        if self.mana_color is not None:value['mana_color']=self.mana_color
        return value

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "DynamicEntryCounterAmountSpec":
        if not isinstance(value, Mapping) or set(value)-{'mana_color'} != {
            "value_source",
            "calculation",
            "coefficient",
            "offset",
            "minimum",
            "quantity",
        }:
            raise EntryCounterError(
                "Dynamic entry counter amount uses a closed schema"
            )
        if 'mana_color' in value and value['value_source']!=DynamicEntryCounterValueSource.MANA_OF_COLOR_SPENT.value:
            raise EntryCounterError('Only paid-color entry amounts may serialize a mana color')
        try:
            quantity = (
                CharacteristicQuantitySpec.from_dict(value["quantity"])
                if value["quantity"] is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise EntryCounterError(str(exc)) from exc
        return cls(
            value_source=value["value_source"],
            calculation=value["calculation"],
            coefficient=value["coefficient"],
            offset=value["offset"],
            minimum=value["minimum"],
            quantity=quantity,
            mana_color=value.get('mana_color'),
        )


def _normalized_nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise EntryCounterError(f"{field} must be a string")
    normalized = " ".join(value.casefold().split())
    if not normalized:
        raise EntryCounterError(f"{field} must be nonempty")
    return normalized


@dataclass(frozen=True, slots=True)
class IntrinsicEntryCounter:
    counter_name: str
    amount: int
    required_type: str | None
    rule_id: str

    def __post_init__(self) -> None:
        counter_name = " ".join(self.counter_name.casefold().split())
        required_type = (
            _normalized_nonempty(self.required_type, field="Intrinsic entry type")
            if self.required_type is not None else None
        )
        rule_id = str(self.rule_id or "")
        if not counter_name or not rule_id:
            raise EntryCounterError("Intrinsic entry counters require a counter and rule")
        if type(self.amount) is not int or self.amount < 0:
            raise EntryCounterError(
                "Intrinsic entry counter amounts must be nonnegative integers"
            )
        object.__setattr__(self, "counter_name", counter_name)
        object.__setattr__(self, "required_type", required_type)
        object.__setattr__(self, "rule_id", rule_id)

    @property
    def capability_id(self) -> str:
        if self.required_type == "saga":
            return "counter.producer.saga_lore"
        if self.required_type is None:
            from .fixed_keyword_entry_counters import FIXED_KEYWORD_ENTRY_CAPABILITY

            return FIXED_KEYWORD_ENTRY_CAPABILITY
        return "counter.producer.intrinsic_entry"


def validate_required_entry_type(value: Any) -> str:
    from .creature_subtypes import canonical_creature_subtype

    if type(value) is not str or (value != "creature" and canonical_creature_subtype(value) != value):
        raise EntryCounterError("Entry counters require a canonical creature type or subtype")
    return value


@dataclass(frozen=True, slots=True)
class EffectEntryCounter:
    """One effect-generated counter attached to a proposed zone entry."""

    counter_name: str
    amount: int
    placing_player: str
    source_ref: str
    rule_id: str
    required_entry_type: str | None = None
    additional: bool = False
    destination: str = "battlefield"

    def __post_init__(self) -> None:
        counter_name = _normalized_nonempty(
            self.counter_name,
            field="Effect entry counter name",
        )
        if self.destination not in {"battlefield", "exile"} or self.destination == "exile" and (
                counter_name != "silver" or self.amount != 1 or self.additional or self.required_entry_type is not None):
            raise EntryCounterError("A marked exile entry requires one silver counter")
        if type(self.additional) is not bool or self.additional and counter_name != "+1/+1":
            raise EntryCounterError("The bounded additional entry policy requires +1/+1 counters")
        if type(self.amount) is not int or self.amount < 1:
            raise EntryCounterError(
                "Effect entry counter amounts must be positive integers"
            )
        for field_name in ("placing_player", "source_ref", "rule_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value != value.strip():
                raise EntryCounterError(
                    f"Effect entry counter {field_name} must be a canonical "
                    "nonempty string"
                )
        object.__setattr__(self, "counter_name", counter_name)
        if self.required_entry_type == "planeswalker":
            if counter_name != "loyalty" or self.amount != 1:
                raise EntryCounterError("The prospective planeswalker result requires one additional loyalty counter")
        elif self.required_entry_type is not None:
            validate_required_entry_type(self.required_entry_type)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "counter_name": self.counter_name,
            "amount": self.amount,
            "placing_player": self.placing_player,
            "source_ref": self.source_ref,
            "rule_id": self.rule_id,
        }
        if self.required_entry_type is not None:
            result["required_entry_type"] = self.required_entry_type
        if self.additional:
            result["additional"] = True
        if self.destination == "exile":
            result["destination"] = self.destination
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EffectEntryCounter":
        if not isinstance(value, Mapping):
            raise EntryCounterError(
                "Effect entry counter serialization must be an object"
            )
        expected = {
            "counter_name",
            "amount",
            "placing_player",
            "source_ref",
            "rule_id",
        }
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected - {"required_entry_type", "additional", "destination"})
        if "destination" in value and value["destination"] != "exile":
            raise EntryCounterError("Only an exile entry counter carries an explicit destination")
        if "additional" in value and value["additional"] is not True:
            raise EntryCounterError("Only explicit additional entry counters take the additional flag")
        if missing or unknown:
            details = [
                *(f"missing {field}" for field in missing),
                *(f"unknown {field}" for field in unknown),
            ]
            raise EntryCounterError(
                "Effect entry counter fields: " + "; ".join(details)
            )
        return cls(
            counter_name=value["counter_name"],
            amount=value["amount"],
            placing_player=value["placing_player"],
            source_ref=value["source_ref"],
            rule_id=value["rule_id"],
            required_entry_type=(
                validate_required_entry_type(value["required_entry_type"])
                if "required_entry_type" in value else None
            ),
            additional=value.get("additional", False),
            destination=value.get("destination", "battlefield"),
        )


def _printed_nonnegative_integer(
    value: Any,
    *,
    characteristic: str,
) -> int:
    if type(value) is int:
        amount = value
    elif type(value) is str and re.fullmatch(r"-?\d+", value.strip()):
        amount = int(value.strip())
    else:
        raise EntryCounterError(
            f"{characteristic} must be a represented nonnegative integer"
        )
    if amount < 0:
        raise EntryCounterError(f"{characteristic} cannot be negative")
    return amount


def intrinsic_entry_counters(
    characteristics: Mapping[str, Any],
    *,
    card_types: Sequence[str],
    card_subtypes: Sequence[str] = (),
    keywords: Sequence[str] = (),
    read_ahead_supported: bool = False,
) -> tuple[IntrinsicEntryCounter, ...]:
    """Return closed rules-derived battlefield entry-counter instructions."""

    if not isinstance(characteristics, Mapping):
        raise EntryCounterError(
            "Entry counter characteristics must be a mapping"
        )
    types = {" ".join(str(value).casefold().split()) for value in card_types}
    subtypes = {
        " ".join(str(value).casefold().split()) for value in card_subtypes
    }
    normalized_keywords = {
        " ".join(str(value).casefold().split()) for value in keywords
    }
    from .fixed_keyword_entry_counters import FIXED_KEYWORD_ENTRY_FRAGMENT, FixedKeywordEntryCounterSpec

    counters: list[IntrinsicEntryCounter] = []
    for fragment in characteristics.get("ability_fragments", ()):
        if isinstance(fragment, Mapping) and fragment.get("kind") == FIXED_KEYWORD_ENTRY_FRAGMENT:
            spec = FixedKeywordEntryCounterSpec.from_dict(fragment.get("value"))
            counters.append(IntrinsicEntryCounter(spec.counter_name, spec.amount, None, spec.rule_id))
    if "planeswalker" in types:
        counters.append(
            IntrinsicEntryCounter(
                counter_name="loyalty",
                amount=_printed_nonnegative_integer(
                    characteristics.get("loyalty"),
                    characteristic="Starting loyalty",
                ),
                required_type="planeswalker",
                rule_id="306.5b",
            )
        )
    if "battle" in types:
        counters.append(
            IntrinsicEntryCounter(
                counter_name="defense",
                amount=_printed_nonnegative_integer(
                    characteristics.get("defense"),
                    characteristic="Battle defense",
                ),
                required_type="battle",
                rule_id="310.4b",
            )
        )
    if "saga" in subtypes and characteristics.get(INTRINSIC_ABILITIES_REMOVED) is not True:
        if "read ahead" in normalized_keywords:
            if read_ahead_supported:
                return tuple(counters)
            raise EntryCounterError(
                "Read Ahead Saga entry requires its unrepresented chapter "
                "number choice"
            )
        counters.append(
            IntrinsicEntryCounter(
                counter_name="lore",
                amount=1,
                required_type="saga",
                rule_id="714.3a",
            )
        )
    return tuple(counters)


__all__ = [
    "DYNAMIC_SELF_ENTRY_AMOUNTS_CONTEXT",
    "DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID",
    "DynamicEntryCounterAmountSpec",
    "DynamicEntryCounterCalculation",
    "DynamicEntryCounterValueSource",
    "EntryCounterError",
    "EffectEntryCounter",
    "IntrinsicEntryCounter",
    "intrinsic_entry_counters",
]
