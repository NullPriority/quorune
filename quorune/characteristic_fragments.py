from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .counter_names import CounterStateError, normalized_counter_name
from .object_predicate import ObjectQueryError, ObjectQuerySpec, object_query_capabilities


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
    LINKED_OPPONENT_HAND = "linked_opponent_hand"
    LINKED_OPPONENT_DEFINITION = "linked_opponent_definition"
    LINKED_PLAYER_GRAVEYARD = "linked_player_graveyard"
    SOURCE_LINKED_EXILE = "source_linked_exile"
    OPPONENT_ZONES = "opponent_zones"
    ALL_ZONES = "all_zones"
    ATTACHED_TO_SOURCE = "attached_to_source"
    ATTACHED_TO_RECIPIENT = "attached_to_recipient"
    EQUIPMENT_ON_CONTROLLED_CREATURES = "equipment_on_controlled_creatures"
    SOURCE_COUNTER = "source_counter"
    CONTROLLER_OPPONENT_COUNT = "controller_opponent_count"
    CONTROLLER_LIFE_TOTAL = "controller_life_total"
    CONTROLLER_TURN_LIFE_LOSS = "controller_turn_life_loss"
    DEFINING_LIFE_TOTAL = "defining_life_total"
    DEFINING_HALF_MAXIMUM_OPPONENT_LIFE = "defining_half_maximum_opponent_life"
    CONTROLLER_TRANSFORMED_PERMANENTS = "controller_transformed_permanents"
    OPPONENT_POISON_COUNTERS = "opponent_poison_counters"
    OPPONENTS_BELOW_HALF_STARTING_LIFE = "opponents_below_half_starting_life"
    PLAYERS_AT_OR_BELOW_HALF_STARTING_LIFE = "players_at_or_below_half_starting_life"
    ATTACHED_OBJECT = "attached_object"
    CONTROLLER_COMMANDER_CAST_COUNT = "controller_commander_cast_count"
    ATTACKING_DEFENDER_ZONE = "attacking_defender_zone"
    COMBAT_STATE_BASIC_LAND_ZONE = "combat_state_basic_land_zone"
    OPPONENT_CHOSEN_COLOR_PERMANENTS = "opponent_chosen_color_permanents"


class CharacteristicQuantityReduction(str, Enum):
    MAXIMUM_ZONE_SIZE = 'maximum_zone_size'
    OBJECT_COUNT = "object_count"
    DISTINCT_BASIC_LAND_TYPES = "distinct_basic_land_types"
    DISTINCT_PUBLIC_NAMES = "distinct_public_names"
    DISTINCT_CARD_TYPES = "distinct_card_types"
    DISTINCT_COLORS = "distinct_colors"
    MAXIMUM_MANA_VALUE = "maximum_mana_value"
    TOTAL_MANA_VALUE = "total_mana_value"
    TOTAL_COUNTERS = "total_counters"
    NAMED_COUNTER_TOTAL = "named_counter_total"
    SUM_QUANTITIES = "sum_quantities"
    COLORED_MANA_SYMBOLS = "colored_mana_symbols"
    DEVOTION = "devotion"


DEFINING_LIFE_SCOPES = frozenset({
    CharacteristicQuantityScope.DEFINING_LIFE_TOTAL,
    CharacteristicQuantityScope.DEFINING_HALF_MAXIMUM_OPPONENT_LIFE,
})
CONTROLLER_TURN_LIFE_LOSS_MODIFIER_TEMPLATE = "continuous-self-controller-turn-life-loss-v1"
SOURCE_LINKED_EXILE_QUANTITY_VERSION = 13
SOURCE_LINKED_EXILE_MODIFIER_TEMPLATE = "continuous-source-linked-exile-count-v1"
CHOSEN_COLOR_DEFINITION_QUANTITY_VERSION = 14
CHOSEN_COLOR_DEFINITION_CAPABILITY = "quantity_expression.opponent_chosen_color_permanents"
CHOSEN_COLOR_DEFINITION_TEMPLATE = "continuous-opponent-chosen-color-definition-v1"
CONTROLLER_TURN_LIFE_LOSS_CAPABILITIES = (
    "condition.history.fixed_public_query", "quantity_expression.current_player_value",
)
PUBLIC_NAMED_COUNT_CAPABILITY = "quantity_expression.public_named_count"
DISTINCT_PUBLIC_NAMES_CAPABILITY = "quantity_expression.distinct_public_names"
NAMED_ATTACHED_EQUIPMENT_CAPABILITY = "condition.attachment.named_equipment_on_controlled_creature"
NAMED_COUNTER_TOTAL_CAPABILITY = "quantity_expression.public_named_counter_total"
ATTACKING_DEFENDER_LAND_COUNT_CAPABILITY = "quantity_expression.attacking_defender_land_count"
COMBAT_STATE_BASIC_LAND_COUNT_CAPABILITY = "quantity_expression.combat_state_basic_land_count"

LINKED_OPPONENT_HAND_QUERY = ObjectQuerySpec(zones=("hand",))
DISTINCT_LAND_NAMES_QUERY = ObjectQuerySpec(zones=("battlefield",), types_all=("land",))
LINKED_OPPONENT_TAPPED_LANDS_QUERY = ObjectQuerySpec(zones=("battlefield",), types_all=("land",), tapped=True)
LINKED_OPPONENT_CREATURES_QUERY = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
_LINKED_OPPONENT_DEFINITION_QUERIES = (
    LINKED_OPPONENT_HAND_QUERY, LINKED_OPPONENT_TAPPED_LANDS_QUERY, LINKED_OPPONENT_CREATURES_QUERY,
)


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
    literal_name: str | None = None

    def __post_init__(self) -> None:
        self._validate_reduction_version()
        self._validate_scope_and_exclusions()
        if (self.schema_version == CHOSEN_COLOR_DEFINITION_QUANTITY_VERSION
                or self.scope is CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS):
            self._validate_chosen_color_definition_count()
            return
        if self.schema_version == SOURCE_LINKED_EXILE_QUANTITY_VERSION or self.scope is CharacteristicQuantityScope.SOURCE_LINKED_EXILE:
            self._validate_source_linked_exile_count()
            return
        if self.schema_version == 12 or self.scope is CharacteristicQuantityScope.LINKED_PLAYER_GRAVEYARD:
            if (
                self.schema_version != 12 or self.scope is not CharacteristicQuantityScope.LINKED_PLAYER_GRAVEYARD
                or self.query is not None or self.counter_name is not None or self.exclude_source
                or self.exclude_attached_object or self.terms or self.mana_colors
                or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                or self.multiplier != 1 or self.offset != 0
            ):
                raise CharacteristicFragmentError("A linked-player graveyard count requires its exact definition quantity")
            return
        if self.schema_version == 11 or self.scope in DEFINING_LIFE_SCOPES:
            if (self.schema_version != 11 or self.scope not in DEFINING_LIFE_SCOPES
                    or self.query is not None or self.counter_name is not None or self.exclude_source
                    or self.exclude_attached_object or self.terms or self.mana_colors
                    or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                    or self.multiplier != 1 or self.offset != 0):
                raise CharacteristicFragmentError("A defining life total requires its exact signed quantity")
            return
        if self.schema_version in {9, 10} or self.scope in {
            CharacteristicQuantityScope.LINKED_OPPONENT_HAND, CharacteristicQuantityScope.LINKED_OPPONENT_DEFINITION,
        }:
            self._validate_linked_opponent_quantity()
            return
        if self.scope in {
            CharacteristicQuantityScope.ATTACKING_DEFENDER_ZONE,
            CharacteristicQuantityScope.COMBAT_STATE_BASIC_LAND_ZONE,
        }:
            self._validate_combat_land_count()
            return
        if self.schema_version == 8:
            from .commander_cast_count_model import validate_commander_cast_quantity
            try:
                validate_commander_cast_quantity(self)
            except ValueError as exc:
                raise CharacteristicFragmentError(str(exc)) from exc
            return
        if self.scope is CharacteristicQuantityScope.CONTROLLER_COMMANDER_CAST_COUNT:
            raise CharacteristicFragmentError("Commander cast counts require their explicit quantity version")
        if self.schema_version == 7 or self.scope is CharacteristicQuantityScope.EQUIPMENT_ON_CONTROLLED_CREATURES:
            self._validate_named_count()
        if self.schema_version==5:
            from .public_zone_quantity_model import validate_public_zone_quantity
            try:validate_public_zone_quantity(self)
            except ValueError as exc:raise CharacteristicFragmentError(str(exc)) from exc
            return
        if self.schema_version == 4:
            self._validate_current_value()
            return
        if self.scope in {CharacteristicQuantityScope.CONTROLLER_LIFE_TOTAL,
                CharacteristicQuantityScope.CONTROLLER_TURN_LIFE_LOSS,
                CharacteristicQuantityScope.OPPONENT_POISON_COUNTERS, CharacteristicQuantityScope.ATTACHED_OBJECT,
                CharacteristicQuantityScope.OPPONENTS_BELOW_HALF_STARTING_LIFE,
                CharacteristicQuantityScope.PLAYERS_AT_OR_BELOW_HALF_STARTING_LIFE}:
            raise CharacteristicFragmentError('Current player and attached-object values require version 4')
        if self.scope is CharacteristicQuantityScope.CONTROLLER_TRANSFORMED_PERMANENTS:
            raise CharacteristicFragmentError("Transformed-permanent counts require version 4")
        if self.schema_version == 3:
            self._validate_sum_terms()
            return
        if self.terms or self.reduction is CharacteristicQuantityReduction.SUM_QUANTITIES:
            raise CharacteristicFragmentError("Only an explicit quantity-sum version carries terms")
        if self.scope is CharacteristicQuantityScope.CONTROLLER_OPPONENT_COUNT:
            if (self.schema_version!=2 or self.query is not None or self.counter_name is not None
                    or self.exclude_source or self.exclude_attached_object
                    or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT):
                raise CharacteristicFragmentError('Opponent counts require a versioned public-roster quantity')
            return
        if self.scope is CharacteristicQuantityScope.SOURCE_COUNTER:
            self._validate_source_counter()
            return
        self._validate_object_query()

    def _validate_linked_opponent_quantity(self) -> None:
        hand = self.scope is CharacteristicQuantityScope.LINKED_OPPONENT_HAND
        if hand:
            valid = (self.schema_version, self.query, self.offset) == (9, None, 0)
        else:
            valid = (
                self.scope is CharacteristicQuantityScope.LINKED_OPPONENT_DEFINITION
                and self.schema_version == 10 and self.query in _LINKED_OPPONENT_DEFINITION_QUERIES
                and self.offset == int(self.query == LINKED_OPPONENT_CREATURES_QUERY)
            )
        if (not valid or self.counter_name is not None or self.exclude_source or self.exclude_attached_object
                or self.terms or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                or self.multiplier != 1):
            raise CharacteristicFragmentError("A linked opponent quantity requires its exact selected-player read")

    def _validate_combat_land_count(self) -> None:
        subtypes = ()
        if self.scope is CharacteristicQuantityScope.COMBAT_STATE_BASIC_LAND_ZONE:
            from .basic_land_types import BASIC_LAND_MANA

            subtypes = self.query.subtypes_any if isinstance(self.query, ObjectQuerySpec) else ()
            if len(subtypes) != 1 or subtypes[0] not in BASIC_LAND_MANA:
                raise CharacteristicFragmentError("A combat-state land count requires one basic land subtype")
        if (
            self.schema_version != 2
            or self.query != ObjectQuerySpec(zones=("battlefield",), types_all=("land",), subtypes_any=subtypes)
            or self.counter_name is not None
            or self.exclude_source
            or self.exclude_attached_object
            or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
            or self.multiplier != 1
            or self.offset != 0
            or self.terms
        ):
            raise CharacteristicFragmentError("An attacking defender quantity requires the closed current land count")

    def _validate_source_linked_exile_count(self) -> None:
        if (
            self.schema_version != SOURCE_LINKED_EXILE_QUANTITY_VERSION
            or self.scope is not CharacteristicQuantityScope.SOURCE_LINKED_EXILE
            or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
            or self.query is not None or self.counter_name is not None or self.exclude_source
            or self.exclude_attached_object or self.terms or self.mana_colors
            or self.multiplier != 1 or self.offset != 0
        ):
            raise CharacteristicFragmentError("Source-linked exile needs its exact unscaled current card count")

    def _validate_chosen_color_definition_count(self) -> None:
        if (
            self.schema_version != CHOSEN_COLOR_DEFINITION_QUANTITY_VERSION
            or self.scope is not CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS
            or self.query is not None or self.counter_name is not None or self.terms
            or self.exclude_source or self.exclude_attached_object
            or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
            or self.multiplier != 1 or self.offset != 0
        ):
            raise CharacteristicFragmentError("A chosen-color definition counts the complete opposing public set")

    def _validate_reduction_version(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {
            1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, SOURCE_LINKED_EXILE_QUANTITY_VERSION,
            CHOSEN_COLOR_DEFINITION_QUANTITY_VERSION,
        }:
            raise CharacteristicFragmentError(
                "Unsupported characteristic quantity schema version"
            )
        if (self.literal_name is not None) != (self.schema_version == 7):
            raise CharacteristicFragmentError("Literal names require their public count version")
        if not isinstance(self.reduction, CharacteristicQuantityReduction) or (
            self.schema_version == 1 and (self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT or self.multiplier != 1 or self.offset != 0)
        ):
            raise CharacteristicFragmentError("Characteristic reduction requires its explicit versioned vocabulary")
        if self.reduction is CharacteristicQuantityReduction.MAXIMUM_ZONE_SIZE and self.schema_version!=5:
            raise CharacteristicFragmentError('Zone maxima require their explicit zone-cardinality version')
        if self.reduction is CharacteristicQuantityReduction.DISTINCT_PUBLIC_NAMES and (
            self.schema_version != 2 or self.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
            or self.query != DISTINCT_LAND_NAMES_QUERY or self.exclude_source or self.exclude_attached_object
        ):
            raise CharacteristicFragmentError("Distinct names require the complete controlled battlefield land query")
        if type(self.multiplier) is not int or self.multiplier < 1 or type(self.offset) is not int or self.offset < 0:
            raise CharacteristicFragmentError("Characteristic reduction arithmetic requires positive multiplier and nonnegative fixed offset")
        if self.reduction in {CharacteristicQuantityReduction.COLORED_MANA_SYMBOLS, CharacteristicQuantityReduction.DEVOTION}:
            if (len(self.mana_colors) not in ({1,2} if self.reduction is CharacteristicQuantityReduction.DEVOTION else {1})
                    or any(color not in {'W','U','B','R','G'} for color in self.mana_colors)
                    or tuple(sorted(set(self.mana_colors))) != tuple(self.mana_colors)):
                raise CharacteristicFragmentError("Colored symbol reductions require one canonical mana color")
            object.__setattr__(self, "mana_colors", tuple(self.mana_colors))
        elif self.mana_colors:
            raise CharacteristicFragmentError("Only colored symbol reductions carry mana colors")

    def _validate_named_count(self) -> None:
        name = self.literal_name
        if (type(name) is not str or not name or name != " ".join(name.split())
                or any(ord(character) < 32 for character in name)):
            raise CharacteristicFragmentError("A named count requires one canonical literal name")
        if self.scope is CharacteristicQuantityScope.EQUIPMENT_ON_CONTROLLED_CREATURES:
            if (self.schema_version != 7 or self.query != ObjectQuerySpec(
                    zones=("battlefield",), types_all=("artifact",), subtypes_all=("equipment",))
                    or self.exclude_source or self.exclude_attached_object or self.counter_name is not None
                    or self.terms or self.multiplier != 1 or self.offset != 0
                    or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT):
                raise CharacteristicFragmentError("Named attached Equipment requires its exact battlefield count")
            return
        query = self.query
        if (not isinstance(query, ObjectQuerySpec) or query.zones not in {("battlefield",), ("graveyard",)}
                or self.scope not in {CharacteristicQuantityScope.CONTROLLER_ZONE,
                    CharacteristicQuantityScope.OPPONENT_ZONES, CharacteristicQuantityScope.ALL_ZONES}
                or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                or self.exclude_attached_object):
            raise CharacteristicFragmentError("Named counts require one public battlefield or graveyard collection")
        if query != ObjectQuerySpec(zones=query.zones, types_all=query.types_all, types_any=query.types_any,
                token=False if query.zones == ("graveyard",) else query.token):
            raise CharacteristicFragmentError("Named counts require a closed type filter and a card-only graveyard")

    def _validate_current_value(self) -> None:
        if (self.query is not None or self.counter_name is not None or self.exclude_source
                or self.exclude_attached_object or self.terms or self.mana_colors
                or self.multiplier != 1 or self.offset != 0):
            raise CharacteristicFragmentError('Current values require one unscaled closed public reference')
        if self.scope in {CharacteristicQuantityScope.CONTROLLER_LIFE_TOTAL,
                CharacteristicQuantityScope.CONTROLLER_TURN_LIFE_LOSS,
                CharacteristicQuantityScope.OPPONENT_POISON_COUNTERS,
                CharacteristicQuantityScope.OPPONENTS_BELOW_HALF_STARTING_LIFE,
                CharacteristicQuantityScope.PLAYERS_AT_OR_BELOW_HALF_STARTING_LIFE}:
            if self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT:
                raise CharacteristicFragmentError('Player values cannot carry an object reduction')
        elif self.scope is CharacteristicQuantityScope.CONTROLLER_TRANSFORMED_PERMANENTS:
            if self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT:
                raise CharacteristicFragmentError("Transformed permanents require an unscaled object count")
        elif self.scope is CharacteristicQuantityScope.ATTACHED_OBJECT:
            if self.reduction not in {CharacteristicQuantityReduction.DISTINCT_COLORS,
                                      CharacteristicQuantityReduction.MAXIMUM_MANA_VALUE,
                                      CharacteristicQuantityReduction.TOTAL_COUNTERS}:
                raise CharacteristicFragmentError("Attached-object values require colors, mana value, or counters")
        else:
            raise CharacteristicFragmentError('Version 4 reserves its explicit current-value scopes')

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
            or term.scope is CharacteristicQuantityScope.COMBAT_STATE_BASIC_LAND_ZONE
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
        if self.reduction is CharacteristicQuantityReduction.TOTAL_COUNTERS:
            if (self.schema_version != 2 or self.counter_name is not None or self.query is not None
                    or self.exclude_source or self.exclude_attached_object):
                raise CharacteristicFragmentError('Source total counters require their explicit public reduction')
            return
        if (
            type(self.counter_name) is not str or not self.counter_name.strip()
            or self.query is not None or self.exclude_source
            or self.exclude_attached_object or self.schema_version not in {1, 2}
            or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
        ):
            raise CharacteristicFragmentError("Source-counter quantities require only one counter name")
        try:
            counter_name = normalized_counter_name(self.counter_name)
        except CounterStateError as exc:
            raise CharacteristicFragmentError(str(exc)) from exc
        object.__setattr__(self, "counter_name", counter_name)

    def _allows_nonartifact_noncolor_count(self, query: ObjectQuerySpec) -> bool:
        return (
            self.schema_version == 2
            and self.scope is CharacteristicQuantityScope.CONTROLLER_ZONE
            and self.reduction is CharacteristicQuantityReduction.OBJECT_COUNT
            and self.multiplier == 1 and self.offset == 0
            and not self.exclude_source and not self.exclude_attached_object
            and len(query.colors_none) == 1
            and query == ObjectQuerySpec(
                zones=("battlefield",), types_all=("creature",), excluded_types=("artifact",),
                colors_none=query.colors_none,
            )
        )

    def _validate_object_query(self) -> None:
        if self.reduction is CharacteristicQuantityReduction.NAMED_COUNTER_TOTAL:
            query = self.query
            controlled_creature_or_land = (
                self.counter_name == "+1/+1" and isinstance(query, ObjectQuerySpec)
                and query.types_all in {("creature",), ("land",)}
                and query == ObjectQuerySpec(zones=("battlefield",), types_all=query.types_all)
            )
            controlled_saga = (
                self.counter_name == "lore" and not self.exclude_source
                and query == ObjectQuerySpec(zones=("battlefield",), subtypes_any=("saga",))
            )
            if (
                self.schema_version != 2
                or self.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
                or self.exclude_attached_object
                or self.multiplier != 1
                or self.offset != 0
                or not (controlled_creature_or_land or controlled_saga)
            ):
                raise CharacteristicFragmentError(
                    "Named counter totals require a closed controlled permanent query"
                )
            return
        if self.reduction is CharacteristicQuantityReduction.TOTAL_COUNTERS and (
                not isinstance(self.query, ObjectQuerySpec) or self.query.zones != ('battlefield',)):
            raise CharacteristicFragmentError('Total counters require current public battlefield objects')
        if self.counter_name is not None or not isinstance(
            self.query, ObjectQuerySpec
        ):
            raise CharacteristicFragmentError(
                "Object quantities require one typed object query"
            )
        query = self.query
        if self.schema_version == 6 and (
                query.zones != ('battlefield',) or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                or self.multiplier != 1 or self.offset != 0 or self.scope not in {
                    CharacteristicQuantityScope.CONTROLLER_ZONE, CharacteristicQuantityScope.OPPONENT_ZONES,
                    CharacteristicQuantityScope.ALL_ZONES} or query.numeric_characteristic is not None
                or query.any_of or query.state_predicate is not None):
            raise CharacteristicFragmentError('Keyword-layer quantities require one closed battlefield object count')
        if self.reduction is CharacteristicQuantityReduction.DEVOTION and (
            self.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
            or query != ObjectQuerySpec(zones=('battlefield',))
            or self.exclude_source or self.exclude_attached_object
        ):
            raise CharacteristicFragmentError('Devotion requires the complete controlled permanent domain')
        if self.scope is CharacteristicQuantityScope.ATTACHED_TO_RECIPIENT and (
            self.schema_version != 2 or self.exclude_source or self.exclude_attached_object
            or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
        ):
            raise CharacteristicFragmentError('Recipient attachment counts require their versioned fixed query')
        if self.schema_version == 2 and (
            query.state_predicate is not None
            or query.tapped is not None or query.minimum_color_count is not None
            or query.excluded_controllers
        ):
            raise CharacteristicFragmentError("Collection reductions require a closed layer-5 public query")
        if query.zones == ('hand',) and self.schema_version == 2 and (
                query != ObjectQuerySpec(zones=('hand',))
                or self.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
                or self.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                or self.exclude_source or self.exclude_attached_object):
            raise CharacteristicFragmentError('Hand arithmetic exposes only the unfiltered controller hand size')
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
            or query.commander is not None
            or query.controller is not None
            or query.exclude_ref is not None
            or query.known_to_actor is not None
            or (query.keywords_all and self.schema_version != 6)
            or (query.colors_none and not self._allows_nonartifact_noncolor_count(query))
            or (query.keywords_none and self.schema_version != 6)
            or query.numeric_characteristic is not None
            or (query.any_of and any(part.exclude_ref is not None for part in query.any_of))
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
            CharacteristicQuantityScope.ATTACHED_TO_RECIPIENT: {"battlefield"},
            CharacteristicQuantityScope.EQUIPMENT_ON_CONTROLLED_CREATURES: {"battlefield"},
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
                "mana_colors": list(self.mana_colors)} if self.schema_version in {2, 3, 4, 5, 6, 7, 8, 9, 10} else {}),
            **({"terms": [term.to_dict() for term in self.terms]} if self.schema_version == 3 else {}),
            **({"literal_name": self.literal_name} if self.schema_version == 7 else {}),
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
        reduced = {*extended, "reduction", "multiplier", "offset", "mana_colors"}
        if not isinstance(value, Mapping) or frozenset(value) not in {
            frozenset(expected),
            frozenset(extended),
            frozenset(reduced),
            frozenset({*reduced, "terms"}),
            frozenset({*reduced, "literal_name"}),
        }:
            raise CharacteristicFragmentError(
                "Characteristic quantities have a closed schema"
            )
        version = value.get("schema_version")
        if version == 11 and set(value) != extended:
            raise CharacteristicFragmentError("Defining life totals have a closed version-eleven schema")
        if version == 12 and set(value) != extended:
            raise CharacteristicFragmentError("Linked-player graveyard definitions have a closed version-twelve schema")
        if version == SOURCE_LINKED_EXILE_QUANTITY_VERSION and set(value) != extended:
            raise CharacteristicFragmentError("Source-linked exile quantities have a closed version-thirteen schema")
        if version == CHOSEN_COLOR_DEFINITION_QUANTITY_VERSION and set(value) != extended:
            raise CharacteristicFragmentError("Chosen-color definitions require their complete versioned quantity")
        if ((version in {2, 3, 4, 5, 6, 7, 8, 9, 10}) != ("reduction" in value)
                or (version == 3) != ("terms" in value) or (version == 7) != ("literal_name" in value)):
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
            literal_name=value.get("literal_name"),
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
    controller_turn_only: bool = False

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise CharacteristicFragmentError(
                "Unsupported query characteristic modifier schema version"
            )
        if type(self.controller_turn_only) is not bool or self.controller_turn_only != (self.schema_version == 2):
            raise CharacteristicFragmentError("Only version two requires the current controller's turn")
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
        if self.quantity.scope is CharacteristicQuantityScope.SOURCE_LINKED_EXILE and (
            self.calculation is not PowerToughnessCalculation.PER_MATCHING_OBJECT
            or (self.power, self.toughness) != (2, 2) or self.minimum_count or abilities or self.controller_turn_only
        ):
            raise CharacteristicFragmentError("Each associated exiled card adds the exact +2/+2 to its source")
        if self.quantity.scope is CharacteristicQuantityScope.LINKED_OPPONENT_HAND and (
            self.calculation is not PowerToughnessCalculation.PER_MATCHING_OBJECT
            or (self.power, self.toughness) != (-1, -1)
            or self.minimum_count or abilities or self.controller_turn_only
        ):
            raise CharacteristicFragmentError("The linked hand count supplies its exact additive reduction")
        if self.quantity.schema_version == 6 and abilities:
            raise CharacteristicFragmentError('Keyword-layer counts cannot supply a same-layer ability grant')
        if self.quantity.scope is CharacteristicQuantityScope.CONTROLLER_TRANSFORMED_PERMANENTS and (
            self.calculation is not PowerToughnessCalculation.PER_MATCHING_OBJECT
            or self.power != 1 or self.toughness != 0 or abilities
        ):
            raise CharacteristicFragmentError("Transformed permanents supply one additive source power per object")
        if self.quantity.scope is CharacteristicQuantityScope.CONTROLLER_TURN_LIFE_LOSS and (
            self.calculation is not PowerToughnessCalculation.PER_MATCHING_OBJECT
            or (self.power, self.toughness) != (1, 0)
            or self.minimum_count or abilities or self.controller_turn_only
        ):
            raise CharacteristicFragmentError("Controller turn life loss supplies exactly one additive source power per life lost")
        if self.quantity.scope in {
            CharacteristicQuantityScope.COMBAT_STATE_BASIC_LAND_ZONE, CharacteristicQuantityScope.LINKED_OPPONENT_DEFINITION,
            CharacteristicQuantityScope.LINKED_PLAYER_GRAVEYARD, *DEFINING_LIFE_SCOPES,
            CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS,
        }:
            raise CharacteristicFragmentError("Definition quantities must define the source's statistics")
        if self.quantity.scope is CharacteristicQuantityScope.ATTACKING_DEFENDER_ZONE and (
            self.calculation is not PowerToughnessCalculation.PER_MATCHING_OBJECT
            or self.power != 1
            or self.toughness != 0
        ):
            raise CharacteristicFragmentError(
                "An attacking defender land count supplies one additive power modifier"
            )
        if self.power == 0 and self.toughness == 0 and not abilities:
            raise CharacteristicFragmentError(
                "Query characteristic modifiers must change characteristics"
            )
        if self.controller_turn_only:
            self._validate_controller_turn_land_modifier()
        elif self.calculation is PowerToughnessCalculation.PER_MATCHING_OBJECT:
            if self.minimum_count != 0 or abilities:
                raise CharacteristicFragmentError(
                    "Per-object modifiers cannot carry a threshold or abilities"
                )
        elif self.minimum_count <= 0:
            raise CharacteristicFragmentError(
                "Threshold modifiers require a positive minimum_count"
            )

    def _validate_controller_turn_land_modifier(self) -> None:
        from .basic_land_types import BASIC_LAND_MANA

        query = self.quantity.query
        if (self.calculation is not PowerToughnessCalculation.PER_MATCHING_OBJECT
                or self.power != 1 or self.toughness != 0 or self.minimum_count <= 0 or self.add_abilities
                or query is None or len(query.subtypes_all) != 1 or query.subtypes_all[0] not in BASIC_LAND_MANA
                or self.quantity != CharacteristicQuantitySpec(
                    CharacteristicQuantityScope.CONTROLLER_ZONE,
                    query=ObjectQuerySpec(zones=("battlefield",), types_all=("land",),
                                          subtypes_all=query.subtypes_all),
                )):
            raise CharacteristicFragmentError(
                "Controller-turn power modifiers require one current basic land subtype and a positive threshold"
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
            **({"controller_turn_only": True} if self.controller_turn_only else {}),
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
        if isinstance(value, Mapping) and value.get("schema_version") == 2:
            expected.add("controller_turn_only")
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
            controller_turn_only=value.get("controller_turn_only", False),
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
            self.quantity.schema_version in {4, 6, 9}
            or self.quantity.scope is CharacteristicQuantityScope.ATTACKING_DEFENDER_ZONE
        ):
            raise CharacteristicFragmentError('Current modifier values do not define characteristic values in layer 7a')
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
        if self.quantity.scope in DEFINING_LIFE_SCOPES and (
            self.schema_version != 1 or not self.define_power or not self.define_toughness
        ):
            raise CharacteristicFragmentError("A defining life total sets both exact statistics without offsets")
        if (not self.define_power and self.power_adjustment) or (not self.define_toughness and self.toughness_adjustment):
            raise CharacteristicFragmentError("Disabled definition fields cannot carry offsets")
        if self.quantity.scope is CharacteristicQuantityScope.COMBAT_STATE_BASIC_LAND_ZONE and (
            self.schema_version != 1 or not self.define_power or not self.define_toughness
        ):
            raise CharacteristicFragmentError("The combat-state land count defines both source stats without offsets")
        if self.quantity.scope is CharacteristicQuantityScope.SOURCE_LINKED_EXILE:
            raise CharacteristicFragmentError("Linked exile counts support additive source modifiers only")
        if self.quantity.scope is CharacteristicQuantityScope.LINKED_PLAYER_GRAVEYARD and (
            self.schema_version != 1 or not self.define_power or not self.define_toughness
        ):
            raise CharacteristicFragmentError("A linked-player graveyard count defines both stats without offsets")
        if self.quantity.scope is CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS and (
            self.schema_version != 1 or not self.define_power or not self.define_toughness
        ):
            raise CharacteristicFragmentError("The chosen-color public count defines both exact source statistics")
        if self.quantity.scope is CharacteristicQuantityScope.LINKED_OPPONENT_DEFINITION and (
            self.schema_version != 1 or not self.define_power
            or self.define_toughness == (self.quantity.query == LINKED_OPPONENT_TAPPED_LANDS_QUERY)
        ):
            raise CharacteristicFragmentError("A linked opponent definition uses its exact printed statistic fields")

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


def nonowner_control_quantity_capabilities(value: Any) -> tuple[str, ...]:
    """Find the existing owner predicate on a typed public quantity."""
    if isinstance(value, Mapping):
        query = value.get("query")
        if (not {"scope", "counter_name", "exclude_source"} <= set(value)
                or not isinstance(query, Mapping) or "controlled_by_nonowner" not in query):
            return ()
        value = CharacteristicQuantitySpec.from_dict(value)
    if not isinstance(value, CharacteristicQuantitySpec) or value.query is None or not value.query.controlled_by_nonowner:
        return ()
    return object_query_capabilities(value.query)


def characteristic_quantity_capabilities(value: Any) -> tuple[str, ...]:
    """Derive producer authority from nested typed quantity descriptors."""
    if isinstance(value, CharacteristicQuantitySpec):
        value = value.to_dict()
    if isinstance(value, Mapping):
        from .public_zone_quantity_model import public_zone_quantity_dependencies
        zone_capabilities=public_zone_quantity_dependencies(value)
        devotion = value.get('reduction') == 'devotion' or (value.get('kind') == 'devotion' and 'color' in value)
        own = (*zone_capabilities, *nonowner_control_quantity_capabilities(value),
               *(('state_query.devotion.current',) if devotion else ()))
        if value.get('schema_version')==6 and {'scope','query','counter_name','exclude_source'}<=set(value):
            CharacteristicQuantitySpec.from_dict(value)
            own=(*own,'quantity_expression.keyword_layer_count')
        if value.get("scope") in ("linked_opponent_hand", "linked_opponent_definition") and {
            "query", "counter_name", "exclude_source",
        } <= set(value):
            from .entry_designations import ENTRY_DESIGNATION_CAPABILITY

            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, ENTRY_DESIGNATION_CAPABILITY)
        if value.get("scope") == CharacteristicQuantityScope.SOURCE_LINKED_EXILE.value and {
            "query", "counter_name", "exclude_source",
        } <= set(value):
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, "zone.exile_until_source_departure.fixed")
        if value.get("scope") == CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS.value and {
            "query", "counter_name", "exclude_source",
        } <= set(value):
            from .entry_designations import ENTRY_DESIGNATION_CAPABILITY

            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, ENTRY_DESIGNATION_CAPABILITY, CHOSEN_COLOR_DEFINITION_CAPABILITY)
        if value.get("scope") == "controller_transformed_permanents" and {
            "query", "counter_name", "exclude_source",
        } <= set(value):
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, "quantity_expression.controlled_transformed_permanents")
        if value.get("scope") == "attacking_defender_zone" and {
            "query", "counter_name", "exclude_source",
        } <= set(value):
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, ATTACKING_DEFENDER_LAND_COUNT_CAPABILITY)
        if value.get("scope") == "combat_state_basic_land_zone" and {
            "query", "counter_name", "exclude_source",
        } <= set(value):
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, COMBAT_STATE_BASIC_LAND_COUNT_CAPABILITY)
        if value.get("reduction") == "named_counter_total" and {
            "scope", "query", "counter_name", "exclude_source",
        } <= set(value):
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, NAMED_COUNTER_TOTAL_CAPABILITY)
        if value.get("reduction") == CharacteristicQuantityReduction.DISTINCT_PUBLIC_NAMES.value:
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, DISTINCT_PUBLIC_NAMES_CAPABILITY)
        if value.get('schema_version')==7 and {'scope','query','counter_name','exclude_source'}<=set(value):
            quantity = CharacteristicQuantitySpec.from_dict(value)
            own=(*own,PUBLIC_NAMED_COUNT_CAPABILITY)
            if quantity.scope is CharacteristicQuantityScope.EQUIPMENT_ON_CONTROLLED_CREATURES:
                own=(*own,NAMED_ATTACHED_EQUIPMENT_CAPABILITY)
        if value.get('schema_version') == 8 and {'scope','query','counter_name','exclude_source'} <= set(value):
            CharacteristicQuantitySpec.from_dict(value)
            from .commander_cast_count_model import COMMANDER_CAST_COUNT_CAPABILITY
            own = (*own, COMMANDER_CAST_COUNT_CAPABILITY)
        if value.get("kind") == "public_query_effect_amount":
            from .query_effect_amount_model import source_relative_count_capabilities

            own = (*own, *source_relative_count_capabilities(value))
        if value.get('scope')=='controller_opponent_count':own=(*own,'quantity_expression.public_player_count')
        if type(value.get("scope")) is str and value["scope"] in DEFINING_LIFE_SCOPES:
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, "quantity_expression.current_player_value")
        if value.get('kind')=='unique_player_maximum' or value.get('fact')=='unique_player_maximum':
            own=(*own,'condition.player.unique_maximum')
        if value.get('fact')=='opponent_has_greater_metric':own=(*own,'condition.player.opponent_greater_metric')
        if value.get("fact") == "controller_unlocked_room_doors":
            from .continuous_conditions import FixedPublicStateConditionSpec
            from .shared_type_doors import ROOM_UNLOCKED_COUNT_CAPABILITY

            FixedPublicStateConditionSpec.from_dict(value)
            own=(*own,ROOM_UNLOCKED_COUNT_CAPABILITY)
        if (value.get('schema_version')==4 and {'scope','query','counter_name','exclude_source'}<=set(value)
                and type(value['scope']) is str
                and value['scope'] in {'controller_life_total','opponent_poison_counters','attached_object'}):
            if value['scope'] in {'controller_life_total','opponent_poison_counters'}:
                own=(*own,'quantity_expression.current_player_value')
            if value["scope"] == "attached_object":
                own = (*own, "quantity_expression.attached_object_value")
                if value.get("reduction") == "total_counters":
                    from .counter_snapshot import COUNTER_TOTAL_CAPABILITY

                    own = (*own, COUNTER_TOTAL_CAPABILITY)
        scope = value.get("scope")
        if type(scope) is str and scope in {"opponents_below_half_starting_life", "players_at_or_below_half_starting_life"} and {
            "query", "counter_name", "exclude_source",
        } <= set(value):
            CharacteristicQuantitySpec.from_dict(value)
            own = (*own, "quantity_expression.player_half_starting_life_threshold")
        if value.get('kind') == 'live_battlefield_quantity':
            from .live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
            live = LiveBattlefieldQuantitySpec.from_dict(value)
            own = (*own, *live.capability_dependencies)
        if value.get('kind')=='controller_is_monarch':own=(*own,'variant.monarch.designate')
        from .continuous_conditions import (
            greatest_artifact_mana_value_condition_dependencies, greatest_creature_power_condition_dependencies,
        )
        from .initiative_model import dungeon_status_dependencies
        own = (
            *own, *dungeon_status_dependencies(value), *greatest_creature_power_condition_dependencies(value),
            *greatest_artifact_mana_value_condition_dependencies(value),
        )
        if (value.get('kind')=='controller_has_citys_blessing'
                and {'schema_version','amount','counter_name'}<=set(value)):
            own=(*own,'player.designation.citys_blessing')
        if value.get('kind')=='controller_max_speed':own=(*own,'variant.speed.player')
        if value.get("kind") == "public_power_aggregate_at_least":
            from .continuous_conditions import FixedPublicStateConditionSpec
            from .public_characteristic_aggregate_model import PUBLIC_POWER_THRESHOLD_CAPABILITY

            condition = FixedPublicStateConditionSpec.from_dict(value)
            own = (*own, PUBLIC_POWER_THRESHOLD_CAPABILITY, *condition.aggregate.capability_dependencies)
        if (value.get('origin') == 'public_characteristic_aggregate'
                or value.get('kind') == 'public_characteristic_aggregate' and 'aggregate' in value):
            from .public_characteristic_aggregate_model import PublicCharacteristicAggregateSpec

            aggregate = PublicCharacteristicAggregateSpec.from_dict(value.get('aggregate'))
            own = (*own, *aggregate.capability_dependencies)
        return tuple(sorted({*own, *(cap for child in value.values() for cap in characteristic_quantity_capabilities(child))}))
    if isinstance(value, (list, tuple)):
        return tuple(sorted({cap for child in value for cap in characteristic_quantity_capabilities(child)}))
    return ()


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
