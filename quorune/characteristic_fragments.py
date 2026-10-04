from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .counter_names import CounterStateError, normalized_counter_name
from .object_predicate import ObjectQueryError, ObjectQuerySpec


class CharacteristicFragmentError(ValueError):
    """A typed dynamic-characteristic fragment is malformed."""


class CharacteristicCountKind(str, Enum):
    CONTROLLER_BATTLEFIELD_ARTIFACTS = (
        "controller_battlefield_artifacts"
    )
    OWNER_GRAVEYARD_CREATURE_CARDS = "owner_graveyard_creature_cards"
    OWNER_GRAVEYARD_LAND_CARDS = "owner_graveyard_land_cards"


class PowerToughnessCalculation(str, Enum):
    PER_MATCHING_OBJECT = "per_matching_object"
    FIXED_IF_THRESHOLD = "fixed_if_threshold"


class CharacteristicQuantityScope(str, Enum):
    """Closed seat/object relation for one characteristic quantity."""

    CONTROLLER_ZONE = "controller_zone"
    OPPONENT_ZONES = "opponent_zones"
    ALL_ZONES = "all_zones"
    ATTACHED_TO_SOURCE = "attached_to_source"
    SOURCE_COUNTER = "source_counter"


class CharacteristicQuantityReduction(str, Enum):
    OBJECT_COUNT = "object_count"
    DISTINCT_BASIC_LAND_TYPES = "distinct_basic_land_types"
    DISTINCT_CARD_TYPES = "distinct_card_types"
    DISTINCT_COLORS = "distinct_colors"
    MAXIMUM_MANA_VALUE = "maximum_mana_value"
    TOTAL_MANA_VALUE = "total_mana_value"
    SUM_QUANTITIES = "sum_quantities"
    COLORED_MANA_SYMBOLS = "colored_mana_symbols"


@dataclass(frozen=True, slots=True)
class CharacteristicQuantitySpec:
    """One cycle-safe public quantity used by a later-layer modifier."""

    scope: CharacteristicQuantityScope
    query: ObjectQuerySpec | None = None
    counter_name: str | None = None
    exclude_source: bool = False
    exclude_attached_object: bool = False
    schema_version: int = 1
    reduction: CharacteristicQuantityReduction = CharacteristicQuantityReduction.OBJECT_COUNT
    multiplier: int = 1
    offset: int = 0
    terms: tuple["CharacteristicQuantitySpec", ...] = ()
    mana_colors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self._validate_reduction_version()
        self._validate_scope_and_exclusions()
        if self.schema_version == 3:
            self._validate_sum_terms()
            return
        if self.terms or self.reduction is CharacteristicQuantityReduction.SUM_QUANTITIES:
            raise CharacteristicFragmentError("Only an explicit quantity-sum version carries terms")
        if self.scope is CharacteristicQuantityScope.SOURCE_COUNTER:
            self._validate_source_counter()
            return
        self._validate_object_query()

    def _validate_reduction_version(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3}:
            raise CharacteristicFragmentError(
                "Unsupported characteristic quantity schema version"
            )
        if not isinstance(self.reduction, CharacteristicQuantityReduction) or (
            self.schema_version == 1 and (self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT or self.multiplier != 1 or self.offset != 0)
        ):
            raise CharacteristicFragmentError("Characteristic reduction requires its explicit versioned vocabulary")
        if type(self.multiplier) is not int or self.multiplier < 1 or type(self.offset) is not int or self.offset < 0:
            raise CharacteristicFragmentError("Characteristic reduction arithmetic requires positive multiplier and nonnegative fixed offset")
        if self.reduction is CharacteristicQuantityReduction.COLORED_MANA_SYMBOLS:
            if len(self.mana_colors) != 1 or self.mana_colors[0] not in {"W", "U", "B", "R", "G"}:
                raise CharacteristicFragmentError("Colored symbol reductions require one canonical mana color")
            object.__setattr__(self, "mana_colors", tuple(self.mana_colors))
        elif self.mana_colors:
            raise CharacteristicFragmentError("Only colored symbol reductions carry mana colors")

    def _validate_scope_and_exclusions(self) -> None:
        if not isinstance(self.scope, CharacteristicQuantityScope):
            raise CharacteristicFragmentError(
                "Unsupported characteristic quantity scope"
            )
        if type(self.exclude_source) is not bool:
            raise CharacteristicFragmentError(
                "Characteristic quantity source exclusion must be boolean"
            )
        if type(self.exclude_attached_object) is not bool:
            raise CharacteristicFragmentError(
                "Characteristic quantity attached-object exclusion must be boolean"
            )
        if self.exclude_source and self.exclude_attached_object:
            raise CharacteristicFragmentError(
                "Characteristic quantities cannot exclude two relative objects"
            )

    def _validate_sum_terms(self) -> None:
        invalid_leaf = any(
            not isinstance(term, CharacteristicQuantitySpec)
            or term.schema_version not in {1, 2}
            or (term.query is not None and term.query.zones == ("hand",))
            for term in self.terms
        )
        if (
            self.reduction is not CharacteristicQuantityReduction.SUM_QUANTITIES
            or self.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
            or self.query is not None or self.counter_name is not None
            or self.exclude_source or self.exclude_attached_object
            or len(self.terms) != 2 or invalid_leaf
        ):
            raise CharacteristicFragmentError("Quantity sums require exactly two closed nonhidden leaf quantities")
        object.__setattr__(self, "terms", tuple(self.terms))

    def _validate_source_counter(self) -> None:
        if (
            type(self.counter_name) is not str or not self.counter_name.strip()
            or self.query is not None or self.exclude_source
            or self.exclude_attached_object or self.schema_version != 1
        ):
            raise CharacteristicFragmentError("Source-counter quantities require only one counter name")
        try:
            counter_name = normalized_counter_name(self.counter_name)
        except CounterStateError as exc:
            raise CharacteristicFragmentError(str(exc)) from exc
        object.__setattr__(self, "counter_name", counter_name)

    def _validate_object_query(self) -> None:
        if self.counter_name is not None or not isinstance(
            self.query, ObjectQuerySpec
        ):
            raise CharacteristicFragmentError(
                "Object quantities require one typed object query"
            )
        query = self.query
        if self.schema_version == 2 and (
            query.zones == ("hand",) or query.state_predicate is not None
            or query.tapped is not None or query.minimum_color_count is not None
            or query.excluded_controllers
        ):
            raise CharacteristicFragmentError("Collection reductions require a closed layer-5 public query")
        if self.reduction is CharacteristicQuantityReduction.DISTINCT_CARD_TYPES and (
            query.zones != ("graveyard",) or query.token is not False
        ):
            raise CharacteristicFragmentError("Distinct graveyard card types require the card-only domain")
        if self.reduction is CharacteristicQuantityReduction.DISTINCT_BASIC_LAND_TYPES and (
            query.zones != ("battlefield",) or query.types_all != ("land",)
        ):
            raise CharacteristicFragmentError("Basic land type reduction requires current battlefield lands")
        if (
            len(query.zones) != 1
            or query.zones[0] not in {"battlefield", "graveyard", "hand"}
            or query.owner is not None
            or query.controller is not None
            or query.exclude_ref is not None
            or query.known_to_actor is not None
            or query.keywords_all
            or query.keywords_none
            or query.include_phased_out
        ):
            raise CharacteristicFragmentError(
                "Characteristic quantities require one closed public zone query"
            )
        if query.zones == ("hand",) and any(
            (
                query.types_all,
                query.types_any,
                query.excluded_types,
                query.subtypes_all,
                query.subtypes_any,
                query.excluded_subtypes,
                query.supertypes_all,
                query.colors_all,
                query.colors_any,
                query.colorless is not None,
                query.token is not None,
            )
        ):
            raise CharacteristicFragmentError(
                "Hand quantities expose only the raw controller hand size"
            )
        if (
            self.scope is CharacteristicQuantityScope.ATTACHED_TO_SOURCE
            and query.zones != ("battlefield",)
        ):
            raise CharacteristicFragmentError(
                "Attachment quantities require a battlefield query"
            )
        allowed_zones = {
            CharacteristicQuantityScope.CONTROLLER_ZONE: {
                "battlefield",
                "graveyard",
                "hand",
            },
            CharacteristicQuantityScope.OPPONENT_ZONES: {
                "battlefield",
                "graveyard",
            },
            CharacteristicQuantityScope.ALL_ZONES: {
                "battlefield",
                "graveyard",
            },
            CharacteristicQuantityScope.ATTACHED_TO_SOURCE: {"battlefield"},
        }
        if query.zones[0] not in allowed_zones[self.scope]:
            raise CharacteristicFragmentError(
                "Characteristic quantity scope does not support that zone"
            )
        if self.exclude_source and query.zones == ("hand",):
            raise CharacteristicFragmentError(
                "Source exclusion is unsupported for hidden hand quantities"
            )
        if self.exclude_attached_object and query.zones != ("battlefield",):
            raise CharacteristicFragmentError(
                "Attached-object exclusion requires a battlefield quantity"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope.value,
            "query": self.query.to_dict() if self.query is not None else None,
            "counter_name": self.counter_name,
            "exclude_source": self.exclude_source,
            "exclude_attached_object": self.exclude_attached_object,
            **({"reduction": self.reduction.value, "multiplier": self.multiplier, "offset": self.offset,
                "mana_colors": list(self.mana_colors)} if self.schema_version in {2, 3} else {}),
            **({"terms": [term.to_dict() for term in self.terms]} if self.schema_version == 3 else {}),
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "CharacteristicQuantitySpec":
        expected = {
            "schema_version",
            "scope",
            "query",
            "counter_name",
            "exclude_source",
        }
        extended = {*expected, "exclude_attached_object"}
        if not isinstance(value, Mapping) or frozenset(value) not in {
            frozenset(expected),
            frozenset(extended),
            frozenset({*extended, "reduction", "multiplier", "offset", "mana_colors"}),
            frozenset({*extended, "reduction", "multiplier", "offset", "mana_colors", "terms"}),
        }:
            raise CharacteristicFragmentError(
                "Characteristic quantities have a closed schema"
            )
        if (value.get("schema_version") in {2, 3}) != ("reduction" in value) or (value.get("schema_version") == 3) != ("terms" in value):
            raise CharacteristicFragmentError("Characteristic reduction fields must match their version")
        try:
            scope = CharacteristicQuantityScope(value["scope"])
            reduction = CharacteristicQuantityReduction(value.get("reduction", "object_count"))
            raw_terms = value.get("terms", ())
            if not isinstance(raw_terms, (tuple, list)):
                raise TypeError("Quantity sum terms must be an array")
            if raw_terms and (len(raw_terms) != 2 or any(not isinstance(term, Mapping) or term.get("schema_version") not in {1, 2} for term in raw_terms)):
                raise ValueError("Quantity sums decode exactly two leaf terms")
            terms = tuple(cls.from_dict(term) for term in raw_terms)
            mana_colors = value.get("mana_colors", ())
            if not isinstance(mana_colors, (list, tuple)):
                raise TypeError("Quantity mana colors must be an array")
            query = (
                ObjectQuerySpec.from_dict(value["query"])
                if value["query"] is not None
                else None
            )
        except (TypeError, ValueError, ObjectQueryError) as exc:
            raise CharacteristicFragmentError(
                "Characteristic quantity vocabulary is unsupported"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            scope=scope,
            query=query,
            counter_name=value["counter_name"],
            exclude_source=value["exclude_source"],
            exclude_attached_object=value.get(
                "exclude_attached_object", False
            ),
            reduction=reduction,
            multiplier=value.get("multiplier", 1),
            offset=value.get("offset", 0),
            terms=terms,
            mana_colors=tuple(mana_colors),
        )


@dataclass(frozen=True, slots=True)
class QueryCharacteristicModifierSpec:
    """One query-counted layer-6/layer-7c self characteristic modifier."""

    quantity: CharacteristicQuantitySpec
    calculation: PowerToughnessCalculation
    power: int
    toughness: int
    minimum_count: int = 0
    add_abilities: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CharacteristicFragmentError(
                "Unsupported query characteristic modifier schema version"
            )
        if not isinstance(self.quantity, CharacteristicQuantitySpec):
            raise CharacteristicFragmentError(
                "Query characteristic modifiers require a typed quantity"
            )
        if not isinstance(self.calculation, PowerToughnessCalculation):
            raise CharacteristicFragmentError(
                "Unsupported query characteristic calculation"
            )
        if type(self.power) is not int or type(self.toughness) is not int:
            raise CharacteristicFragmentError(
                "Query characteristic modifiers require integer amounts"
            )
        if type(self.minimum_count) is not int or self.minimum_count < 0:
            raise CharacteristicFragmentError(
                "Query characteristic minimum_count must be nonnegative"
            )
        abilities = tuple(self.add_abilities)
        if (
            any(type(value) is not str or not value for value in abilities)
            or len(set(abilities)) != len(abilities)
        ):
            raise CharacteristicFragmentError(
                "Query characteristic abilities must be unique strings"
            )
        object.__setattr__(self, "add_abilities", abilities)
        if self.power == 0 and self.toughness == 0 and not abilities:
            raise CharacteristicFragmentError(
                "Query characteristic modifiers must change characteristics"
            )
        if self.calculation is PowerToughnessCalculation.PER_MATCHING_OBJECT:
            if self.minimum_count != 0 or abilities:
                raise CharacteristicFragmentError(
                    "Per-object modifiers cannot carry a threshold or abilities"
                )
        elif self.minimum_count <= 0:
            raise CharacteristicFragmentError(
                "Threshold modifiers require a positive minimum_count"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "quantity": self.quantity.to_dict(),
            "calculation": self.calculation.value,
            "power": self.power,
            "toughness": self.toughness,
            "minimum_count": self.minimum_count,
            "add_abilities": list(self.add_abilities),
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "QueryCharacteristicModifierSpec":
        expected = {
            "schema_version",
            "quantity",
            "calculation",
            "power",
            "toughness",
            "minimum_count",
            "add_abilities",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise CharacteristicFragmentError(
                "Query characteristic modifiers have a closed schema"
            )
        try:
            calculation = PowerToughnessCalculation(value["calculation"])
            quantity = CharacteristicQuantitySpec.from_dict(value["quantity"])
            raw_abilities = value["add_abilities"]
            if not isinstance(raw_abilities, (list, tuple)):
                raise TypeError("add_abilities must be an array")
            abilities = tuple(raw_abilities)
        except (TypeError, ValueError, CharacteristicFragmentError) as exc:
            raise CharacteristicFragmentError(
                "Query characteristic modifier vocabulary is unsupported"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            quantity=quantity,
            calculation=calculation,
            power=value["power"],
            toughness=value["toughness"],
            minimum_count=value["minimum_count"],
            add_abilities=abilities,
        )


@dataclass(frozen=True, slots=True)
class QueryPowerToughnessDefinitionSpec:
    """One all-zone query-derived layer-7a power/toughness definition."""

    quantity: CharacteristicQuantitySpec
    define_power: bool
    define_toughness: bool
    schema_version: int = 1
    power_adjustment: int = 0
    toughness_adjustment: int = 0

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise CharacteristicFragmentError(
                "Unsupported query power/toughness definition schema version"
            )
        if any(type(value) is not int or value < 0 for value in (self.power_adjustment, self.toughness_adjustment)) or (
            self.schema_version == 1 and (self.power_adjustment or self.toughness_adjustment)
        ):
            raise CharacteristicFragmentError("Definition offsets require fixed nonnegative versioned integers")
        if not isinstance(self.quantity, CharacteristicQuantitySpec):
            raise CharacteristicFragmentError(
                "Query power/toughness definitions require a typed quantity"
            )
        if (
            type(self.define_power) is not bool
            or type(self.define_toughness) is not bool
        ):
            raise CharacteristicFragmentError(
                "Query power/toughness definition fields must be boolean"
            )
        if not self.define_power and not self.define_toughness:
            raise CharacteristicFragmentError(
                "Query power/toughness definitions require at least one field"
            )
        if (not self.define_power and self.power_adjustment) or (not self.define_toughness and self.toughness_adjustment):
            raise CharacteristicFragmentError("Disabled definition fields cannot carry offsets")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "quantity": self.quantity.to_dict(),
            "define_power": self.define_power,
            "define_toughness": self.define_toughness,
            **({"power_adjustment": self.power_adjustment, "toughness_adjustment": self.toughness_adjustment} if self.schema_version == 2 else {}),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "QueryPowerToughnessDefinitionSpec":
        expected = {
            "schema_version",
            "quantity",
            "define_power",
            "define_toughness",
        }
        extended = {*expected, "power_adjustment", "toughness_adjustment"}
        if not isinstance(value, Mapping) or set(value) not in (expected, extended) or ((value.get("schema_version") == 2) != (set(value) == extended)):
            raise CharacteristicFragmentError(
                "Query power/toughness definitions have a closed schema"
            )
        try:
            quantity = CharacteristicQuantitySpec.from_dict(value["quantity"])
        except (TypeError, ValueError, CharacteristicFragmentError) as exc:
            raise CharacteristicFragmentError(
                "Query power/toughness definition vocabulary is unsupported"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            quantity=quantity,
            define_power=value["define_power"],
            define_toughness=value["define_toughness"],
            power_adjustment=value.get("power_adjustment", 0),
            toughness_adjustment=value.get("toughness_adjustment", 0),
        )


@dataclass(frozen=True, slots=True)
class ColorlessCharacteristicDefinitionSpec:
    """One closed all-zone layer-5 colorless definition."""

    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CharacteristicFragmentError(
                "Unsupported colorless characteristic-definition schema version"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version}

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "ColorlessCharacteristicDefinitionSpec":
        if not isinstance(value, Mapping) or set(value) != {"schema_version"}:
            raise CharacteristicFragmentError(
                "Colorless characteristic definitions have a closed schema"
            )
        return cls(schema_version=value["schema_version"])


@dataclass(frozen=True, slots=True)
class AllCreatureTypesCharacteristicDefinitionSpec:
    """One closed all-zone layer-4 all-creature-types definition."""

    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CharacteristicFragmentError(
                "Unsupported all-creature-types definition schema version"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "AllCreatureTypesCharacteristicDefinitionSpec":
        if not isinstance(value, Mapping) or set(value) != {"schema_version"}:
            raise CharacteristicFragmentError(
                "All-creature-types definitions have a closed schema"
            )
        return cls(schema_version=value["schema_version"])


@dataclass(frozen=True, slots=True)
class ConditionalKeywordSpec:
    """One closed keyword condition evaluated from public match state."""

    keyword: str
    opponent_life_at_most: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CharacteristicFragmentError(
                "Unsupported conditional-keyword schema version"
            )
        if self.keyword != "Haste":
            raise CharacteristicFragmentError(
                "Conditional-keyword fragments currently support Haste"
            )
        if (
            type(self.opponent_life_at_most) is not int
            or self.opponent_life_at_most < 0
        ):
            raise CharacteristicFragmentError(
                "Conditional-keyword life thresholds must be nonnegative integers"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "keyword": self.keyword,
            "opponent_life_at_most": self.opponent_life_at_most,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConditionalKeywordSpec":
        expected = {
            "schema_version",
            "keyword",
            "opponent_life_at_most",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise CharacteristicFragmentError(
                "Conditional-keyword fragments have a closed schema"
            )
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class DynamicPowerToughnessSpec:
    """One closed count-derived layer-7 characteristic modifier."""

    count_kind: CharacteristicCountKind
    calculation: PowerToughnessCalculation
    power: int
    toughness: int
    minimum_count: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CharacteristicFragmentError(
                "Unsupported dynamic power/toughness schema version"
            )
        if not isinstance(self.count_kind, CharacteristicCountKind):
            raise CharacteristicFragmentError(
                "Unsupported dynamic characteristic count kind"
            )
        if not isinstance(self.calculation, PowerToughnessCalculation):
            raise CharacteristicFragmentError(
                "Unsupported dynamic power/toughness calculation"
            )
        if type(self.power) is not int or type(self.toughness) is not int:
            raise CharacteristicFragmentError(
                "Dynamic power/toughness modifiers must be integers"
            )
        if self.power == 0 and self.toughness == 0:
            raise CharacteristicFragmentError(
                "Dynamic power/toughness must modify at least one value"
            )
        if type(self.minimum_count) is not int or self.minimum_count < 0:
            raise CharacteristicFragmentError(
                "Dynamic power/toughness minimum_count must be nonnegative"
            )
        if (
            self.calculation is PowerToughnessCalculation.PER_MATCHING_OBJECT
            and self.minimum_count != 0
        ):
            raise CharacteristicFragmentError(
                "Per-object modifiers do not carry a threshold"
            )
        if (
            self.calculation is PowerToughnessCalculation.FIXED_IF_THRESHOLD
            and self.minimum_count <= 0
        ):
            raise CharacteristicFragmentError(
                "Threshold modifiers require a positive minimum_count"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "count_kind": self.count_kind.value,
            "calculation": self.calculation.value,
            "power": self.power,
            "toughness": self.toughness,
            "minimum_count": self.minimum_count,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "DynamicPowerToughnessSpec":
        expected = {
            "schema_version",
            "count_kind",
            "calculation",
            "power",
            "toughness",
            "minimum_count",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise CharacteristicFragmentError(
                "Dynamic power/toughness fragments have a closed schema"
            )
        try:
            count_kind = CharacteristicCountKind(value["count_kind"])
            calculation = PowerToughnessCalculation(value["calculation"])
        except (TypeError, ValueError) as exc:
            raise CharacteristicFragmentError(
                "Unsupported dynamic power/toughness vocabulary"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            count_kind=count_kind,
            calculation=calculation,
            power=value["power"],
            toughness=value["toughness"],
            minimum_count=value["minimum_count"],
        )


__all__ = [
    "AllCreatureTypesCharacteristicDefinitionSpec",
    "CharacteristicCountKind",
    "CharacteristicFragmentError",
    "CharacteristicQuantityScope",
    "CharacteristicQuantitySpec",
    "ColorlessCharacteristicDefinitionSpec",
    "ConditionalKeywordSpec",
    "DynamicPowerToughnessSpec",
    "PowerToughnessCalculation",
    "QueryCharacteristicModifierSpec",
    "QueryPowerToughnessDefinitionSpec",
]
