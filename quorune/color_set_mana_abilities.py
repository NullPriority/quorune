from __future__ import annotations

"""Typed activated mana outputs derived from a current object color set.

The compiler owns the narrow Oracle grammar.  Runtime code consumes only this
immutable descriptor and current effective object characteristics; it never
reinterprets Oracle text.
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from .activation_usage import ActivationLimit
from .rules.activation_counter_cost import SourceCounterRemovalCost
from .object_predicate import ObjectQuerySpec
from .linked_exile_return_model import validate_linked_ability_binding
from .rules.linked_hand_mana_model import MANA_HANDLER, OUTPUT, normalized
from .rules.linked_graveyard_mana import COLOR_RELATION, MANA_HANDLER as GRAVEYARD_MANA_HANDLER, OUTPUT as GRAVEYARD_OUTPUT
from .replacement.immutable import FrozenMap, thaw_value


COLOR_SET_MANA_HANDLER_ID = "ability.activated.mana.color-set.v1"
COMMANDER_IDENTITY_MANA_HANDLER_ID = "ability.activated.mana.commander-identity.v1"
COMMANDER_TYPE_SCRY_MANA_HANDLER_ID = "ability.activated.mana.commander-type-scry.v1"
COMMANDER_ENTRY_COUNTER_MANA_HANDLER_ID = "ability.activated.mana.commander-entry-counters.v1"
COMMANDER_IDENTITY_MANA_CAPABILITY = "mana.activated.commander_identity"
MANA_COST_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_COMMANDER_IDENTITY_OUTPUT = "add one mana of any color in your commander's color identity."
_COMMANDER_TYPE_SCRY_OUTPUT = (
    _COMMANDER_IDENTITY_OUTPUT
    + " when that mana is spent to cast a creature spell that shares a creature type with your commander, scry 1."
)

_COMMANDER_ENTRY_COUNTER_OUTPUT = (
    _COMMANDER_IDENTITY_OUTPUT
    + " if you spend this mana to cast your commander, it enters with a number of additional "
      "+1/+1 counters on it equal to the number of times it's been cast from the command zone this game."
)
_COMMANDER_ENTRY_MANA_COST = {"GENERIC": 1, **dict.fromkeys("WUBRGC", 0)}


class ColorSetManaAbilityError(ValueError):
    """A color-set mana descriptor is malformed or unsupported."""


class ColorSetSelection(str, Enum):
    CHOOSE_ONE = "choose_one"
    ONE_EACH = "one_each"


class ColorSetRelation(str, Enum):
    CONTROLLER = "controller"
    OWNER = "owner"
    COMMANDER_IDENTITY = "commander_identity"
    LINKED_EXILE = "linked_exile"
    LINKED_GRAVEYARD_EXILE = COLOR_RELATION


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, field: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise ColorSetManaAbilityError(
            f"{field} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise ColorSetManaAbilityError(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


def _closed_query(
    *,
    zones: tuple[str, ...],
    types_all: tuple[str, ...] = (),
    types_any: tuple[str, ...] = (),
    supertypes_all: tuple[str, ...] = (),
) -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=zones,
        types_all=types_all,
        types_any=types_any,
        supertypes_all=supertypes_all,
        known_to_actor=True,
    )


_SUPPORTED_QUERY_SHAPES = frozenset(
    {
        (
            ColorSetRelation.CONTROLLER,
            ColorSetSelection.CHOOSE_ONE,
            ("battlefield",),
            (),
            ("creature", "planeswalker"),
            ("legendary",),
        ),
        (
            ColorSetRelation.CONTROLLER,
            ColorSetSelection.CHOOSE_ONE,
            ("battlefield",),
            (),
            (),
            ("legendary",),
        ),
        (
            ColorSetRelation.CONTROLLER,
            ColorSetSelection.ONE_EACH,
            ("battlefield",),
            (),
            (),
            (),
        ),
        (
            ColorSetRelation.OWNER,
            ColorSetSelection.CHOOSE_ONE,
            ("graveyard",),
            ("creature",),
            (),
            ("legendary",),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ColorSetActivatedManaAbilitySpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    effect_text: str
    mana_cost: FrozenMap
    tap_source: bool
    sacrifice_source: bool
    life_payment: int
    relation: ColorSetRelation
    selection: ColorSetSelection
    query: ObjectQuerySpec | None
    activation_limit: ActivationLimit | None = None
    source_counter_removal_cost: SourceCounterRemovalCost | None = None
    commander_type_scry: bool = False
    commander_entry_counters: bool = False
    linked_exile_pair: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ability_id, str)
            or _ABILITY_ID.fullmatch(self.ability_id) is None
        ):
            raise ColorSetManaAbilityError("Color-set mana ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise ColorSetManaAbilityError(
                "Color-set mana ability line_index must be nonnegative"
            )
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.oracle_line,
                self.cost_text,
                self.effect_text,
            )
        ):
            raise ColorSetManaAbilityError(
                "Color-set mana ability text fields must be nonempty"
            )
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise ColorSetManaAbilityError(
                    "Color-set mana activation cost must be an object"
                )
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        if set(mana) != set(MANA_COST_KEYS) or any(
            type(value) is not int or value < 0 for value in mana.values()
        ):
            raise ColorSetManaAbilityError(
                "Color-set mana activation cost must contain canonical mana keys"
            )
        if type(self.tap_source) is not bool or type(self.sacrifice_source) is not bool:
            raise ColorSetManaAbilityError(
                "Color-set mana source-cost flags must be booleans"
            )
        if type(self.life_payment) is not int or self.life_payment < 0:
            raise ColorSetManaAbilityError(
                "Color-set mana life payment must be a nonnegative integer"
            )
        if not isinstance(self.relation, ColorSetRelation):
            try:
                object.__setattr__(self, "relation", ColorSetRelation(self.relation))
            except (TypeError, ValueError) as exc:
                raise ColorSetManaAbilityError(
                    "Color-set mana relation is unsupported"
                ) from exc
        if not isinstance(self.selection, ColorSetSelection):
            try:
                object.__setattr__(
                    self, "selection", ColorSetSelection(self.selection)
                )
            except (TypeError, ValueError) as exc:
                raise ColorSetManaAbilityError(
                    "Color-set mana selection is unsupported"
                ) from exc
        if self.query is not None and not isinstance(self.query, ObjectQuerySpec):
            if not isinstance(self.query, Mapping):
                raise ColorSetManaAbilityError(
                    "Color-set mana query must be a typed object query"
                )
            object.__setattr__(self, "query", ObjectQuerySpec.from_dict(self.query))
        if self.activation_limit is not None and not isinstance(
            self.activation_limit, ActivationLimit
        ):
            try:
                object.__setattr__(
                    self,
                    "activation_limit",
                    ActivationLimit(self.activation_limit),
                )
            except (TypeError, ValueError) as exc:
                raise ColorSetManaAbilityError(
                    "Color-set mana activation limit is unsupported"
                ) from exc
        if self.source_counter_removal_cost is not None and not isinstance(
            self.source_counter_removal_cost, SourceCounterRemovalCost
        ):
            raise ColorSetManaAbilityError(
                "Color-set mana source counter cost must be typed or null"
            )
        if type(self.commander_type_scry) is not bool or (
            self.commander_type_scry and (not self.uses_commander_identity or self.activation_limit is not None)
        ):
            raise ColorSetManaAbilityError("Commander-type Scry requires its exact commander mana ability")
        if type(self.commander_entry_counters) is not bool or (
            self.commander_entry_counters and (
                not self.uses_commander_identity or self.commander_type_scry or self.activation_limit is not None
            )
        ):
            raise ColorSetManaAbilityError("Commander entry counters require their exact commander mana ability")
        if self.relation not in {ColorSetRelation.LINKED_EXILE, ColorSetRelation.LINKED_GRAVEYARD_EXILE} and self.linked_exile_pair is not None:
            raise ColorSetManaAbilityError("Only linked exile mana has a linked pair")
        self._validate_query()

    @property
    def uses_commander_identity(self) -> bool:
        return self.relation is ColorSetRelation.COMMANDER_IDENTITY

    def _validate_query(self) -> None:
        query = self.query
        if self.uses_commander_identity:
            if (
                query is not None or self.selection is not ColorSetSelection.CHOOSE_ONE
                or not self.tap_source or self.sacrifice_source or self.life_payment
                or (dict(self.mana_cost) != _COMMANDER_ENTRY_MANA_COST
                    if self.commander_entry_counters else any(self.mana_cost.values()))
                or self.source_counter_removal_cost is not None
            ):
                raise ColorSetManaAbilityError("Commander identity mana requires its exact payment and color choice")
            return
        if self.relation in {ColorSetRelation.LINKED_EXILE, ColorSetRelation.LINKED_GRAVEYARD_EXILE}:
            self._validate_linked_exile_output()
            return
        if query is None:
            raise ColorSetManaAbilityError("Object color-set mana requires its typed query")
        if query.owner is not None or query.controller is not None:
            raise ColorSetManaAbilityError(
                "Color-set mana query principal is bound only at activation time"
            )
        if query.known_to_actor is not True or query.include_phased_out:
            raise ColorSetManaAbilityError(
                "Color-set mana queries require known, nonphased objects"
            )
        if query.zones not in {("battlefield",), ("graveyard",)}:
            raise ColorSetManaAbilityError(
                "Color-set mana query uses an unsupported zone"
            )
        if self.relation is ColorSetRelation.CONTROLLER and query.zones != (
            "battlefield",
        ):
            raise ColorSetManaAbilityError(
                "Controller-relative color-set mana requires the battlefield"
            )
        if self.relation is ColorSetRelation.OWNER and query.zones != (
            "graveyard",
        ):
            raise ColorSetManaAbilityError(
                "Owner-relative color-set mana requires the graveyard"
            )
        if any(
            (
                query.excluded_types,
                query.subtypes_all,
                query.colors_all,
                query.colors_any,
                query.colors_none,
                query.keywords_all,
                query.token is not None,
                query.tapped is not None,
                query.exclude_ref is not None,
            )
        ):
            raise ColorSetManaAbilityError(
                "Color-set mana query exceeds the closed supported predicate grammar"
            )
        shape = (
            self.relation,
            self.selection,
            query.zones,
            query.types_all,
            query.types_any,
            query.supertypes_all,
        )
        if shape not in _SUPPORTED_QUERY_SHAPES:
            raise ColorSetManaAbilityError(
                "Color-set mana query is not a supported canonical shape"
            )

    def _validate_linked_exile_output(self):
        validate_linked_ability_binding(self.linked_exile_pair)
        output = GRAVEYARD_OUTPUT if self.relation is ColorSetRelation.LINKED_GRAVEYARD_EXILE else OUTPUT
        if (self.query is not None or self.selection is not ColorSetSelection.CHOOSE_ONE
                or self.cost_text.casefold() != "{t}" or normalized(self.effect_text) != output
                or normalized(self.oracle_line) != "{t}: " + output
                or not self.tap_source or self.sacrifice_source or self.life_payment
                or any(self.mana_cost.values()) or self.activation_limit is not None
                or self.source_counter_removal_cost is not None):
            raise ColorSetManaAbilityError("Linked exile mana requires its exact tap and one-color output")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "effect_text": self.effect_text,
            "mana_cost": thaw_value(self.mana_cost),
            "tap_source": self.tap_source,
            "sacrifice_source": self.sacrifice_source,
            "life_payment": self.life_payment,
            "relation": self.relation.value,
            "selection": self.selection.value,
            "query": None if self.query is None else self.query.to_dict(),
        }
        if self.activation_limit is not None:
            value["activation_limit"] = self.activation_limit.value
        if self.source_counter_removal_cost is not None:
            value["source_counter_removal_cost"] = (
                self.source_counter_removal_cost.to_dict()
            )
        if self.commander_type_scry:
            value["commander_type_scry"] = True
        if self.commander_entry_counters:
            value["commander_entry_counters"] = True
        if self.linked_exile_pair is not None:
            value["linked_exile_pair"] = self.linked_exile_pair
        return value

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "ColorSetActivatedManaAbilitySpec":
        expected = {
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "effect_text",
            "mana_cost",
            "tap_source",
            "sacrifice_source",
            "life_payment",
            "relation",
            "selection",
            "query",
        }
        if "activation_limit" in value:
            expected.add("activation_limit")
        if "source_counter_removal_cost" in value:
            expected.add("source_counter_removal_cost")
        if "commander_type_scry" in value:
            if value["commander_type_scry"] is not True:
                raise ColorSetManaAbilityError("An absent commander Scry rider must be omitted")
            expected.add("commander_type_scry")
        if "commander_entry_counters" in value:
            if value["commander_entry_counters"] is not True:
                raise ColorSetManaAbilityError("An absent commander entry rider must be omitted")
            expected.add("commander_entry_counters")
        if "linked_exile_pair" in value:
            validate_linked_ability_binding(value["linked_exile_pair"])
            expected.add("linked_exile_pair")
        _exact_fields(value, expected, field="color-set mana ability")
        mana_cost = value["mana_cost"]
        query = value["query"]
        raw_source_counter_cost = value.get("source_counter_removal_cost")
        if not isinstance(mana_cost, Mapping):
            raise ColorSetManaAbilityError(
                "Color-set mana activation cost must be an object"
            )
        if query is not None and not isinstance(query, Mapping):
            raise ColorSetManaAbilityError(
                "Color-set mana query must be an object"
            )
        if raw_source_counter_cost is not None and not isinstance(
            raw_source_counter_cost, Mapping
        ):
            raise ColorSetManaAbilityError(
                "Color-set mana source counter cost must be an object"
            )
        for field in ("ability_id", "oracle_line", "cost_text", "effect_text"):
            if not isinstance(value[field], str):
                raise ColorSetManaAbilityError(
                    f"Color-set mana ability {field} must be a string"
                )
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            effect_text=value["effect_text"],
            mana_cost=FrozenMap(mana_cost),
            tap_source=value["tap_source"],
            sacrifice_source=value["sacrifice_source"],
            life_payment=value["life_payment"],
            relation=value["relation"],
            selection=value["selection"],
            query=None if query is None else ObjectQuerySpec.from_dict(query),
            commander_type_scry=value.get("commander_type_scry", False),
            commander_entry_counters=value.get("commander_entry_counters", False),
            linked_exile_pair=value.get("linked_exile_pair"),
            activation_limit=value.get("activation_limit"),
            source_counter_removal_cost=(
                None
                if raw_source_counter_cost is None
                else SourceCounterRemovalCost.from_dict(
                    raw_source_counter_cost
                )
            ),
        )

    def to_activated_ability(self) -> Any:
        from .abilities import ActivatedAbility

        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            effect_text=self.effect_text,
            zones=("battlefield",),
            mana=thaw_value(self.mana_cost),
            tap_source=self.tap_source,
            sacrifice_source=self.sacrifice_source,
            life_payment=self.life_payment,
            mana_ability=True,
            color_set_mana_output=self,
            activation_limit=self.activation_limit,
            source_counter_removal_cost=self.source_counter_removal_cost,
        )


_TEMPLATES: dict[
    str, tuple[ColorSetRelation, ColorSetSelection, ObjectQuerySpec | None]
] = {
    _COMMANDER_IDENTITY_OUTPUT: (
        ColorSetRelation.COMMANDER_IDENTITY, ColorSetSelection.CHOOSE_ONE, None,
    ),
    (
        "add one mana of any color among legendary creatures and "
        "planeswalkers you control."
    ): (
        ColorSetRelation.CONTROLLER,
        ColorSetSelection.CHOOSE_ONE,
        _closed_query(
            zones=("battlefield",),
            types_any=("creature", "planeswalker"),
            supertypes_all=("legendary",),
        ),
    ),
    "add one mana of any color among legendary permanents you control.": (
        ColorSetRelation.CONTROLLER,
        ColorSetSelection.CHOOSE_ONE,
        _closed_query(
            zones=("battlefield",),
            supertypes_all=("legendary",),
        ),
    ),
    "for each color among permanents you control, add one mana of that color.": (
        ColorSetRelation.CONTROLLER,
        ColorSetSelection.ONE_EACH,
        _closed_query(zones=("battlefield",)),
    ),
    "add one mana of any color among legendary creature cards in your graveyard.": (
        ColorSetRelation.OWNER,
        ColorSetSelection.CHOOSE_ONE,
        _closed_query(
            zones=("graveyard",),
            types_all=("creature",),
            supertypes_all=("legendary",),
        ),
    ),
}


def compile_color_set_activated_mana_ability(
    ability: Any,
) -> ColorSetActivatedManaAbilitySpec | None:
    """Lower one exact color-set output within the closed grammar."""

    effect = " ".join(str(ability.effect_text).split()).casefold()
    commander_type_scry = effect == _COMMANDER_TYPE_SCRY_OUTPUT
    commander_entry_counters = effect == _COMMANDER_ENTRY_COUNTER_OUTPUT
    template = _TEMPLATES.get(
        _COMMANDER_IDENTITY_OUTPUT if commander_type_scry or commander_entry_counters else effect
    )
    if template is None:
        return None
    if (
        not ability.mana_ability
        or not ability.compiled_cost
        or tuple(ability.zones) != ("battlefield",)
        or ability.complex_symbols
        or ability.untap_source
        or ability.discard_source
        or ability.exile_source
        or ability.energy_payment
        or ability.random_discard_payment
        or ability.loyalty_delta is not None
        or ability.choices
        or ability.uncompiled_costs
        or ability.sorcery_speed
        or ability.generic_reduction_per_legendary_creature
        or ability.builtin_semantic_key is not None
        or ability.target_schema is not None
        or ability.crew_threshold is not None
        or not ability.tap_source
        or ability.sacrifice_source
        or ability.life_payment
        or (dict(ability.mana) != _COMMANDER_ENTRY_MANA_COST
            if commander_entry_counters else any(int(ability.mana.get(key, 0)) for key in MANA_COST_KEYS))
        or ((commander_type_scry or commander_entry_counters) and ability.activation_limit is not None)
        or (commander_entry_counters and ability.source_counter_removal_cost is not None)
    ):
        return None
    relation, selection, query = template
    return ColorSetActivatedManaAbilitySpec(
        ability_id=ability.ability_id,
        line_index=ability.line_index,
        oracle_line=ability.oracle_line,
        cost_text=ability.cost_text,
        effect_text=ability.effect_text,
        mana_cost=FrozenMap(
            {key: int(ability.mana.get(key, 0)) for key in MANA_COST_KEYS}
        ),
        tap_source=ability.tap_source,
        sacrifice_source=ability.sacrifice_source,
        life_payment=ability.life_payment,
        relation=relation,
        selection=selection,
        query=query,
        activation_limit=ability.activation_limit,
        source_counter_removal_cost=ability.source_counter_removal_cost,
        commander_type_scry=commander_type_scry,
        commander_entry_counters=commander_entry_counters,
    )


def color_set_mana_handler_descriptor(
    spec: ColorSetActivatedManaAbilitySpec,
) -> dict[str, Any]:
    handler = COMMANDER_IDENTITY_MANA_HANDLER_ID if spec.uses_commander_identity else COLOR_SET_MANA_HANDLER_ID
    if spec.commander_type_scry:
        handler = COMMANDER_TYPE_SCRY_MANA_HANDLER_ID
    elif spec.commander_entry_counters:
        handler = COMMANDER_ENTRY_COUNTER_MANA_HANDLER_ID
    elif spec.relation is ColorSetRelation.LINKED_GRAVEYARD_EXILE:
        handler = GRAVEYARD_MANA_HANDLER
    elif spec.linked_exile_pair is not None:
        handler = MANA_HANDLER
    return {
        "handler_id": handler,
        "schema_version": 1,
        "event": "activate",
        "ability": spec.to_dict(),
    }


__all__ = [
    "COLOR_SET_MANA_HANDLER_ID",
    "ColorSetActivatedManaAbilitySpec",
    "ColorSetManaAbilityError",
    "ColorSetRelation",
    "ColorSetSelection",
    "color_set_mana_handler_descriptor",
    "compile_color_set_activated_mana_ability",
]
