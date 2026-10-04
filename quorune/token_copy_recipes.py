from __future__ import annotations

"""Immutable fixed copy recipes; this module owns no game-state mutation."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from .characteristic_evaluation import type_parts
from .creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from .keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .replacement.immutable import FrozenMap, thaw_value


class TokenCopyRecipeError(ValueError):
    """A copy instruction exceeds its explicitly represented vocabulary."""


def _unique_words(values: Any, *, allowed: set[str] | frozenset[str], label: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise TokenCopyRecipeError(f"{label} must be an array")
    result = tuple(values)
    if any(type(value) is not str or value not in allowed for value in result) or len(set(result)) != len(result):
        raise TokenCopyRecipeError(f"{label} has an unsupported or duplicate value")
    return result


@dataclass(frozen=True, slots=True)
class TokenCopyExceptionSpec:
    remove_legendary: bool = False
    add_card_types: tuple[str, ...] = ()
    add_subtypes: tuple[str, ...] = ()
    creature_subtypes: tuple[str, ...] | None = None
    colors: tuple[str, ...] | None = None
    power: int | None = None
    toughness: int | None = None
    add_keywords: tuple[str, ...] = ()
    add_ability_fragments: tuple[Mapping[str, Any], ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 or type(self.remove_legendary) is not bool:
            raise TokenCopyRecipeError("Copy exception version or legendary flag is malformed")
        if (self.power is None) != (self.toughness is None) or any(
            value is not None and (type(value) is not int or value < 0)
            for value in (self.power, self.toughness)
        ):
            raise TokenCopyRecipeError("Copy base stats require two nonnegative fixed integers")
        for field_name, allowed in (
            ("add_card_types", frozenset({"artifact", "creature", "enchantment"})),
            ("add_subtypes", CREATURE_SUBTYPES | {"food", "equipment"}),
            ("add_keywords", FIXED_CHARACTERISTIC_KEYWORDS),
        ):
            object.__setattr__(self, field_name, _unique_words(getattr(self, field_name), allowed=allowed, label=field_name))
        if self.creature_subtypes is not None:
            subtypes = _unique_words(self.creature_subtypes, allowed=CREATURE_SUBTYPES, label="creature_subtypes")
            if not subtypes or any(canonical_creature_subtype(value) != value for value in subtypes):
                raise TokenCopyRecipeError("A creature subtype replacement must be canonical and nonempty")
            object.__setattr__(self, "creature_subtypes", subtypes)
        if self.colors is not None:
            colors = _unique_words(self.colors, allowed=frozenset("WUBRG"), label="colors")
            if colors != tuple(color for color in "WUBRG" if color in colors):
                raise TokenCopyRecipeError("Copy colors must use canonical order")
            object.__setattr__(self, "colors", colors)
        from .ability_fragments import ability_fragment_to_dict, canonical_ability_fragments
        if not isinstance(self.add_ability_fragments, (list, tuple)) or len(self.add_ability_fragments) > 2:
            raise TokenCopyRecipeError("Copy grants require a bounded fragment array")
        fragments = canonical_ability_fragments(self.add_ability_fragments)
        object.__setattr__(self, "add_ability_fragments", tuple(FrozenMap(ability_fragment_to_dict(fragment)) for fragment in fragments))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "remove_legendary": self.remove_legendary,
            "add_card_types": list(self.add_card_types), "add_subtypes": list(self.add_subtypes),
            "creature_subtypes": list(self.creature_subtypes) if self.creature_subtypes is not None else None,
            "colors": list(self.colors) if self.colors is not None else None,
            "power": self.power, "toughness": self.toughness, "add_keywords": list(self.add_keywords),
            "add_ability_fragments": [thaw_value(fragment) for fragment in self.add_ability_fragments]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenCopyExceptionSpec":
        fields = {"schema_version", "remove_legendary", "add_card_types", "add_subtypes", "creature_subtypes", "colors", "power", "toughness", "add_keywords", "add_ability_fragments"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise TokenCopyRecipeError("Copy exceptions have a closed complete schema")
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class TokenCopyRecipeSpec:
    origin: str
    exception: TokenCopyExceptionSpec = TokenCopyExceptionSpec()
    cleanup: str = "none"
    temporary_keywords: tuple[str, ...] = ()
    keyword_duration: str = "zone_object"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise TokenCopyRecipeError("Copy recipe version is unsupported")
        if self.origin not in {"source", "target", "event_object"} or not isinstance(self.exception, TokenCopyExceptionSpec):
            raise TokenCopyRecipeError("Copy reference origin or exception is unsupported")
        if self.cleanup not in {"none", "sacrifice_next_end_step", "exile_next_end_step"}:
            raise TokenCopyRecipeError("Copy cleanup is outside the represented lifecycle")
        if self.keyword_duration not in {"zone_object", "until_end_of_turn"}:
            raise TokenCopyRecipeError("Copy keyword duration is unsupported")
        object.__setattr__(self, "temporary_keywords", _unique_words(self.temporary_keywords,
            allowed=FIXED_CHARACTERISTIC_KEYWORDS, label="temporary_keywords"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "origin": self.origin,
                "exception": self.exception.to_dict(), "cleanup": self.cleanup,
                "temporary_keywords": list(self.temporary_keywords), "keyword_duration": self.keyword_duration}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenCopyRecipeSpec":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "origin", "exception", "cleanup", "temporary_keywords", "keyword_duration"}:
            raise TokenCopyRecipeError("Copy recipes have a closed complete schema")
        return cls(schema_version=value["schema_version"], origin=value["origin"],
                   exception=TokenCopyExceptionSpec.from_dict(value["exception"]), cleanup=value["cleanup"],
                   temporary_keywords=value["temporary_keywords"], keyword_duration=value["keyword_duration"])


def apply_copy_exception(characteristics: Mapping[str, Any], exception: TokenCopyExceptionSpec) -> dict[str, Any]:
    """Apply CR 707.9 values before creation; never copy status or counters."""
    result = deepcopy(dict(characteristics))
    card_types, subtypes, supertypes = type_parts(str(result.get("type_line") or ""))
    if exception.remove_legendary:
        supertypes.discard("legendary")
    card_types.update(exception.add_card_types)
    if exception.creature_subtypes is not None:
        subtypes.difference_update(CREATURE_SUBTYPES)
        subtypes.update(exception.creature_subtypes)
    subtypes.update(exception.add_subtypes)
    if exception.remove_legendary or exception.add_card_types or exception.add_subtypes or exception.creature_subtypes is not None:
        ordered_types = [value for value in ("kindred", "artifact", "battle", "creature", "enchantment", "land", "planeswalker") if value in card_types]
        left = " ".join(value.title() for value in (*sorted(supertypes), *ordered_types))
        result["type_line"] = left + (" — " + " ".join(value.title() for value in sorted(subtypes)) if subtypes else "")
    if exception.power is not None:
        result["power"], result["toughness"] = str(exception.power), str(exception.toughness)
    if exception.colors is not None:
        result["colors"] = list(exception.colors)
        result.pop("color_indicator", None)
    fragments = result.get("ability_fragments", ())
    if not isinstance(fragments, (list, tuple)) or any(not isinstance(value, Mapping) for value in fragments):
        raise TokenCopyRecipeError("Copied ability fragments are unavailable")
    result["ability_fragments"] = [deepcopy(dict(fragment)) for fragment in fragments if not (
        exception.power is not None and fragment.get("kind") == "query_power_toughness_definition"
        or exception.colors is not None and fragment.get("kind") == "colorless_characteristic_definition"
        or exception.creature_subtypes is not None and fragment.get("kind") == "all_creature_types_characteristic_definition"
    )]
    result["ability_fragments"].extend(thaw_value(fragment) for fragment in exception.add_ability_fragments)
    keywords = list(result.get("keywords", ()))
    if exception.colors is not None:
        keywords = [value for value in keywords if str(value).casefold() != "devoid"]
    if exception.creature_subtypes is not None:
        keywords = [value for value in keywords if str(value).casefold() != "changeling"]
    result["keywords"] = list(dict.fromkeys((*keywords, *exception.add_keywords)))
    return result
