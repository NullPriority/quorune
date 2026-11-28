from __future__ import annotations

"""Typed Cycling and Typecycling activation descriptors.

The represented grammar covers ordinary fixed mana and one exact variable
self-discard price. Other variable, hybrid, Phyrexian, snow, and nonmana
prices remain outside these descriptors.
"""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .fixed_mana_abilities import MANA_COST_KEYS
from .replacement.immutable import FrozenMap, thaw_value
from .util import mana_cost_to_vector


CYCLING_HANDLER_ID = "ability.activated.cycling.v1"
VARIABLE_CYCLING_HANDLER_ID = "ability.activated.cycling.v2"
VARIABLE_CYCLING_CAPABILITY = "activation.cycling.announced_x"
VARIABLE_CYCLING_PRICE = "{X}{1}{U}"
TYPECYCLING_HANDLER_ID = "ability.activated.typecycling.v1"
CYCLING_MECHANIC_ID = "cycling"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*(?::typecycling-instance:[1-4])?$")
_ORDINARY_COST = r"(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+"
_ORDINARY_CYCLING = re.compile(
    rf"^Cycling\s+(?P<cost>{_ORDINARY_COST})\.?$",
    re.IGNORECASE,
)
_VARIABLE_CYCLING = re.compile(rf"^Cycling\s+(?P<cost>{re.escape(VARIABLE_CYCLING_PRICE)})\.?$", re.IGNORECASE)
_TYPECYCLING_LABELS: dict[str, tuple[str, dict[str, list[str]]]] = {
    "basic land": (
        "basic land",
        {"types": ["land"], "supertypes": ["basic"]},
    ),
    "plains": (
        "Plains",
        {"types": ["land"], "subtypes_any": ["plains"]},
    ),
    "island": (
        "Island",
        {"types": ["land"], "subtypes_any": ["island"]},
    ),
    "swamp": (
        "Swamp",
        {"types": ["land"], "subtypes_any": ["swamp"]},
    ),
    "mountain": (
        "Mountain",
        {"types": ["land"], "subtypes_any": ["mountain"]},
    ),
    "forest": (
        "Forest",
        {"types": ["land"], "subtypes_any": ["forest"]},
    ),
    "artifact land": (
        "artifact land",
        {"types": ["artifact", "land"]},
    ),
    "wizard": ("Wizard", {"subtypes_any": ["wizard"]}),
    "sliver": ("Sliver", {"subtypes_any": ["sliver"]}),
}
_TYPECYCLING_LABEL_PATTERN = "|".join(re.escape(value) for value in _TYPECYCLING_LABELS)
PRINTED_CYCLING_KEYWORD_PATTERN = re.compile(rf"\b(?:{_TYPECYCLING_LABEL_PATTERN})?cycling\b", re.IGNORECASE)
_TYPECYCLING = re.compile(
    rf"^(?P<label>{_TYPECYCLING_LABEL_PATTERN})"
    rf"cycling\s+(?P<cost>{_ORDINARY_COST})\.?$",
    re.IGNORECASE,
)


class CyclingAbilityError(ValueError):
    """An ordinary Cycling descriptor is malformed or unsupported."""


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], *, field: str
) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise CyclingAbilityError(
            f"{field} is missing required fields: {', '.join(missing)}"
        )
    if unknown:
        raise CyclingAbilityError(
            f"{field} has unknown fields: {', '.join(unknown)}"
        )


def _validate_fixed_mana_fields(
    *,
    ability_id: object,
    line_index: object,
    oracle_line: object,
    cost_text: object,
    mana_cost: object,
) -> FrozenMap:
    if (
        not isinstance(ability_id, str)
        or _ABILITY_ID.fullmatch(ability_id) is None
    ):
        raise CyclingAbilityError("Cycling ability ID requires a line identity and optional typecycling instance")
    if type(line_index) is not int or line_index < 0:
        raise CyclingAbilityError("Cycling ability line_index must be nonnegative")
    if not isinstance(oracle_line, str) or not oracle_line:
        raise CyclingAbilityError("Cycling ability oracle_line must be nonempty")
    if (
        not isinstance(cost_text, str)
        or re.fullmatch(_ORDINARY_COST, cost_text, re.IGNORECASE) is None
    ):
        raise CyclingAbilityError(
            "Cycling cost must contain only fixed ordinary mana symbols"
        )
    if isinstance(mana_cost, FrozenMap):
        frozen = mana_cost
    elif isinstance(mana_cost, Mapping):
        frozen = FrozenMap(mana_cost)
    else:
        raise CyclingAbilityError("Cycling mana cost must be an object")
    mana = thaw_value(frozen)
    if set(mana) != set(MANA_COST_KEYS) or any(
        type(amount) is not int or amount < 0 for amount in mana.values()
    ):
        raise CyclingAbilityError(
            "Cycling mana cost must contain canonical nonnegative keys"
        )
    expected, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols or mana != expected:
        raise CyclingAbilityError(
            "Cycling mana cost does not match the printed cost"
        )
    return frozen


@dataclass(frozen=True, slots=True)
class OrdinaryCyclingAbilitySpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    announced_x_symbols: int | None = None

    def __post_init__(self) -> None:
        fixed_price = self.cost_text
        if self.announced_x_symbols is not None:
            if (type(self.announced_x_symbols) is not int or self.announced_x_symbols != 1
                    or self.cost_text != VARIABLE_CYCLING_PRICE):
                raise CyclingAbilityError("Variable Cycling requires its exact ordinary X and fixed mana")
            fixed_price = self.cost_text.removeprefix("{X}")
        frozen = _validate_fixed_mana_fields(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=fixed_price,
            mana_cost=self.mana_cost,
        )
        object.__setattr__(self, "mana_cost", frozen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_cost": thaw_value(self.mana_cost),
            **({"announced_x_symbols": self.announced_x_symbols} if self.announced_x_symbols is not None else {}),
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "OrdinaryCyclingAbilitySpec":
        _exact_fields(
            value,
            {
                "ability_id",
                "line_index",
                "oracle_line",
                "cost_text",
                "mana_cost",
            } | ({"announced_x_symbols"} if "announced_x_symbols" in value else set()),
            field="ordinary Cycling ability",
        )
        if "announced_x_symbols" in value and type(value["announced_x_symbols"]) is not int:
            raise CyclingAbilityError("Variable Cycling requires its integer X symbol count")
        if not all(
            isinstance(value[field], str)
            for field in ("ability_id", "oracle_line", "cost_text")
        ):
            raise CyclingAbilityError(
                "Cycling ability text fields must be strings"
            )
        mana_cost = value["mana_cost"]
        if not isinstance(mana_cost, Mapping):
            raise CyclingAbilityError("Cycling mana cost must be an object")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(mana_cost),
            announced_x_symbols=value.get("announced_x_symbols"),
        )

    def to_activated_ability(self) -> Any:
        from .abilities import ActivatedAbility

        cost_text = self.cost_text
        if self.announced_x_symbols is not None:
            cost_text += ", Discard this card"
        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=cost_text,
            effect_text="Draw a card.",
            zones=("hand",),
            mana=thaw_value(self.mana_cost),
            discard_source=True,
            announced_x_symbols=self.announced_x_symbols,
        )


@dataclass(frozen=True, slots=True)
class TypecyclingAbilitySpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    label: str
    search_selector: FrozenMap

    def __post_init__(self) -> None:
        frozen_mana = _validate_fixed_mana_fields(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            mana_cost=self.mana_cost,
        )
        object.__setattr__(self, "mana_cost", frozen_mana)
        if not isinstance(self.label, str):
            raise CyclingAbilityError("Typecycling label must be a string")
        definition = _TYPECYCLING_LABELS.get(self.label.casefold())
        if definition is None:
            raise CyclingAbilityError("Typecycling label is unsupported")
        canonical_label, selector = definition
        object.__setattr__(self, "label", canonical_label)
        frozen_selector = (
            self.search_selector
            if isinstance(self.search_selector, FrozenMap)
            else FrozenMap(self.search_selector)
            if isinstance(self.search_selector, Mapping)
            else None
        )
        if frozen_selector is None or thaw_value(frozen_selector) != selector:
            raise CyclingAbilityError(
                "Typecycling selector does not match its printed label"
            )
        object.__setattr__(self, "search_selector", frozen_selector)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_cost": thaw_value(self.mana_cost),
            "label": self.label,
            "search_selector": thaw_value(self.search_selector),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TypecyclingAbilitySpec":
        _exact_fields(
            value,
            {
                "ability_id",
                "line_index",
                "oracle_line",
                "cost_text",
                "mana_cost",
                "label",
                "search_selector",
            },
            field="Typecycling ability",
        )
        for field in ("ability_id", "oracle_line", "cost_text", "label"):
            if not isinstance(value[field], str):
                raise CyclingAbilityError(
                    "Typecycling ability text fields must be strings"
                )
        if not isinstance(value["mana_cost"], Mapping):
            raise CyclingAbilityError("Cycling mana cost must be an object")
        if not isinstance(value["search_selector"], Mapping):
            raise CyclingAbilityError("Typecycling selector must be an object")
        return cls(
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(value["mana_cost"]),
            label=value["label"],
            search_selector=FrozenMap(value["search_selector"]),
        )

    def to_activated_ability(self) -> Any:
        from .abilities import ActivatedAbility

        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            effect_text=(
                f"Search your library for a {self.label} card, reveal it, "
                "put it into your hand, then shuffle."
            ),
            zones=("hand",),
            mana=thaw_value(self.mana_cost),
            discard_source=True,
        )

    def search_effect(self) -> dict[str, Any]:
        return {
            "op": "search",
            "zone": "library",
            "selector": thaw_value(self.search_selector),
            "count": {"minimum": 1, "maximum": 1},
            "destination": "hand",
            "reveal": True,
            "shuffle_after": True,
        }


def compile_ordinary_cycling_ability(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> OrdinaryCyclingAbilitySpec | None:
    """Compile one closed fixed-mana Cycling line or return ``None``."""

    variable = _VARIABLE_CYCLING.fullmatch(material_line.strip())
    match = variable or _ORDINARY_CYCLING.fullmatch(material_line.strip())
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    fixed_price = cost_text.removeprefix("{X}") if variable else cost_text
    mana_cost, complex_symbols = mana_cost_to_vector(fixed_price)
    if complex_symbols:
        return None
    return OrdinaryCyclingAbilitySpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_cost=FrozenMap(mana_cost),
        announced_x_symbols=1 if variable else None,
    )


def compile_typecycling_ability(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> TypecyclingAbilitySpec | None:
    """Compile one closed fixed-mana Typecycling line or return ``None``."""

    match = _TYPECYCLING.fullmatch(material_line.strip())
    if match is None:
        return None
    definition = _TYPECYCLING_LABELS[match.group("label").casefold()]
    cost_text = match.group("cost").upper()
    mana_cost, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return TypecyclingAbilitySpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        cost_text=cost_text,
        mana_cost=FrozenMap(mana_cost),
        label=definition[0],
        search_selector=FrozenMap(definition[1]),
    )


def ordinary_cycling_handler_descriptor(
    spec: OrdinaryCyclingAbilitySpec,
) -> dict[str, Any]:
    variable = spec.announced_x_symbols is not None
    return {
        "handler_id": VARIABLE_CYCLING_HANDLER_ID if variable else CYCLING_HANDLER_ID,
        "schema_version": 2 if variable else 1,
        "event": "activate",
        "ability": spec.to_dict(),
    }


def typecycling_handler_descriptor(
    spec: TypecyclingAbilitySpec,
) -> dict[str, Any]:
    return {
        "handler_id": TYPECYCLING_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        "ability": spec.to_dict(),
    }


__all__ = [
    "CYCLING_HANDLER_ID",
    "CYCLING_MECHANIC_ID",
    "TYPECYCLING_HANDLER_ID",
    "CyclingAbilityError",
    "OrdinaryCyclingAbilitySpec",
    "TypecyclingAbilitySpec",
    "compile_ordinary_cycling_ability",
    "compile_typecycling_ability",
    "ordinary_cycling_handler_descriptor",
    "typecycling_handler_descriptor",
]
