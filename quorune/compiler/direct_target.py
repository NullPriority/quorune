from __future__ import annotations

"""Typed structural helpers for independently owned direct-target grammars."""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from ..object_predicate import (
    ObjectQueryError,
    PermanentStatePredicateSpec,
)
from .creature_subtypes import canonical_creature_subtype


CompiledDirectTarget = tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any],
    tuple[str, ...],
]


_DIRECT_TYPE_ANY_VALUES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "land",
        "planeswalker",
    }
)
DIRECT_NONCREATURE_SUBTYPES = frozenset({"forest", "gate", "vehicle"})
DIRECT_PERMANENT_TYPES = _DIRECT_TYPE_ANY_VALUES | {"permanent"}
_DIRECT_SUPERTYPES = frozenset({"basic", "legendary", "snow"})
_DIRECT_KEYWORDS = frozenset(
    {"defender", "flying", "horsemanship", "islandwalk", "shadow"}
)
_DIRECT_COLORS = frozenset({"W", "U", "B", "R", "G"})
_DIRECT_COLOR_WORDS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_OUTLAW_SUBTYPES = ("assassin", "mercenary", "pirate", "rogue", "warlock")


def _canonical_terms(
    values: Sequence[str],
    *,
    field: str,
) -> tuple[str, ...]:
    normalized = tuple(
        sorted(value.casefold() for value in _closed_values(values, field=field))
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"Direct-target {field} values must be unique")
    return normalized


def _canonical_colors(
    values: Sequence[str],
    *,
    field: str,
) -> tuple[str, ...]:
    normalized = tuple(
        sorted(value.upper() for value in _closed_values(values, field=field))
    )
    if len(set(normalized)) != len(normalized) or not set(
        normalized
    ).issubset(_DIRECT_COLORS):
        raise ValueError(f"Direct-target {field} values are unsupported")
    return normalized


def _validate_mana_value_predicate(
    spec: "DirectPermanentTargetSpec",
) -> None:
    values = (
        spec.mana_value_min,
        spec.mana_value_max,
        spec.mana_value_equal,
    )
    if sum(value is not None for value in values) > 1 or any(
        value is not None and (type(value) is not int or value < 0)
        for value in values
    ):
        raise ValueError("Direct permanent mana-value predicate is unsupported")
    if any(value is not None for value in values) and spec.state_predicate is not None:
        raise ValueError(
            "Direct permanent mana-value predicates cannot mix public "
            "state predicates"
        )


def _validate_public_state_predicate(
    spec: "DirectPermanentTargetSpec",
) -> None:
    state = spec.state_predicate
    if state is None:
        return
    if not isinstance(state, PermanentStatePredicateSpec):
        raise ValueError("Direct permanent public-state predicate must be typed")
    state_kinds = sum(
        (
            state.entered_this_turn,
            state.tapped is not None,
            state.counter_name is not None,
        )
    )
    invalid_creature_state = (
        state.entered_this_turn or state.tapped is not None
    ) and spec.types_any != ("creature",)
    invalid_counter_state = (
        state.counter_name is not None
        and spec.types_any not in {(), ("creature",)}
    )
    if state_kinds != 1 or invalid_creature_state or invalid_counter_state:
        raise ValueError("Direct permanent public-state predicate is unsupported")


def _strip_mana_value_predicate(
    phrase: str,
) -> tuple[str, dict[str, int]]:
    match = re.fullmatch(
        r"(?P<body>.+) with mana value (?P<value>\d+)"
        r"(?: or (?P<direction>less|greater))?",
        phrase,
    )
    if match is None:
        return phrase, {}
    direction = match.group("direction")
    field = (
        "mana_value_max"
        if direction == "less"
        else "mana_value_min"
        if direction == "greater"
        else "mana_value_equal"
    )
    return match.group("body"), {field: int(match.group("value"))}


def _split_direct_or_terms(value: str) -> tuple[str, ...]:
    return tuple(
        term.strip()
        for term in re.split(r",\s*(?:or\s+)?|\s+or\s+", value)
        if term.strip()
    )


def _simple_direct_type_fields(phrase: str) -> dict[str, Any] | None:
    if phrase in DIRECT_PERMANENT_TYPES:
        return {} if phrase == "permanent" else {"types_any": (phrase,)}
    terms = _split_direct_or_terms(phrase)
    if len(terms) > 1 and set(terms).issubset(_DIRECT_TYPE_ANY_VALUES):
        return {"types_any": terms}
    conjunction = tuple(phrase.split())
    if (
        len(conjunction) == 2
        and set(conjunction).issubset(_DIRECT_TYPE_ANY_VALUES)
    ):
        return {"types_all": conjunction}
    return None


def _closed_direct_characteristic_fields(
    phrase: str,
) -> dict[str, Any] | None:
    """Parse the bounded reusable public-characteristic target grammar."""

    if phrase == (
        "legendary permanent that's an artifact, creature, or enchantment"
    ):
        return {
            "types_any": ("artifact", "creature", "enchantment"),
            "supertypes_any": ("legendary",),
        }
    if phrase == "noncreature artifact or noncreature enchantment":
        return {
            "types_any": ("artifact", "enchantment"),
            "types_none": ("creature",),
        }
    if phrase == "multicolored creature or multicolored enchantment":
        return {
            "types_any": ("creature", "enchantment"),
            "color_count_min": 2,
        }
    if phrase == "non-outlaw creature":
        return {
            "types_any": ("creature",),
            "subtypes_none": _OUTLAW_SUBTYPES,
        }
    if phrase in {"creature token", "token creature"}:
        return {"types_any": ("creature",), "token": True}

    keyword_fields: dict[str, tuple[str, ...]] = {}
    keyword_match = re.fullmatch(
        r"(?P<body>.+) (?P<relation>with|without) "
        r"(?P<keyword>defender|flying|horsemanship|islandwalk|shadow)",
        phrase,
    )
    if keyword_match is not None:
        phrase = keyword_match.group("body")
        keyword_fields[
            "keywords_all"
            if keyword_match.group("relation") == "with"
            else "keywords_none"
        ] = (keyword_match.group("keyword"),)

    one_or_more = re.fullmatch(
        r"(?P<body>.+?)(?: that's| that is)? one or more colors",
        phrase,
    )
    if one_or_more is not None:
        fields = _simple_direct_type_fields(one_or_more.group("body"))
        return (
            {**fields, "color_count_min": 1, **keyword_fields}
            if fields is not None
            else None
        )

    quality_match = re.fullmatch(
        r"(?P<quality>legendary|snow|basic|nonlegendary|nonsnow|nonbasic) "
        r"(?P<body>.+)",
        phrase,
    )
    if quality_match is not None:
        fields = _simple_direct_type_fields(quality_match.group("body"))
        if fields is None:
            return None
        quality = quality_match.group("quality")
        negative = quality.startswith("non")
        supertype = quality[3:] if negative else quality
        return {
            **fields,
            "supertypes_none" if negative else "supertypes_any": (supertype,),
            **keyword_fields,
        }

    cardinality_match = re.fullmatch(
        r"(?P<quality>monocolored|multicolored) (?P<body>.+)",
        phrase,
    )
    if cardinality_match is not None:
        fields = _simple_direct_type_fields(cardinality_match.group("body"))
        if fields is None:
            return None
        return {
            **fields,
            (
                "color_count_equal"
                if cardinality_match.group("quality") == "monocolored"
                else "color_count_min"
            ): 1 if cardinality_match.group("quality") == "monocolored" else 2,
            **keyword_fields,
        }

    color_match = re.fullmatch(
        r"(?P<colors>(?:white|blue|black|red|green)"
        r"(?: or (?:white|blue|black|red|green))*) (?P<body>.+)",
        phrase,
    )
    if color_match is not None:
        fields = _simple_direct_type_fields(color_match.group("body"))
        colors = tuple(
            _DIRECT_COLOR_WORDS[value]
            for value in color_match.group("colors").split(" or ")
        )
        return (
            {**fields, "colors_any": colors, **keyword_fields}
            if fields is not None
            else None
        )

    negative_color_match = re.fullmatch(
        r"non(?P<color>white|blue|black|red|green) (?P<body>.+)",
        phrase,
    )
    if negative_color_match is not None:
        fields = _simple_direct_type_fields(negative_color_match.group("body"))
        return (
            {
                **fields,
                "colors_none": (
                    _DIRECT_COLOR_WORDS[negative_color_match.group("color")],
                ),
                **keyword_fields,
            }
            if fields is not None
            else None
        )

    if phrase.startswith("colorless "):
        fields = _simple_direct_type_fields(phrase[len("colorless ") :])
        return (
            {**fields, "colorless": True, **keyword_fields}
            if fields is not None
            else None
        )

    negative_type_match = re.fullmatch(
        r"non(?P<excluded>artifact|creature|enchantment|land) (?P<body>.+)",
        phrase,
    )
    if negative_type_match is not None:
        fields = _simple_direct_type_fields(negative_type_match.group("body"))
        return (
            {
                **fields,
                "types_none": (negative_type_match.group("excluded"),),
                **keyword_fields,
            }
            if fields is not None
            else None
        )

    fields = _simple_direct_type_fields(phrase)
    if fields is not None:
        return {**fields, **keyword_fields}
    return None


_DIRECT_TERM_FIELDS = (
    "types_any",
    "types_all",
    "types_none",
    "subtypes_any",
    "subtypes_none",
    "supertypes_any",
    "supertypes_none",
    "keywords_all",
    "keywords_none",
)


def _canonicalize_direct_target_spec(
    spec: "DirectPermanentTargetSpec",
) -> None:
    for field_name in _DIRECT_TERM_FIELDS:
        object.__setattr__(
            spec,
            field_name,
            _canonical_terms(getattr(spec, field_name), field=field_name),
        )
    for field_name in ("colors_any", "colors_none"):
        object.__setattr__(
            spec,
            field_name,
            _canonical_colors(getattr(spec, field_name), field=field_name),
        )


def _positive_direct_types(
    spec: "DirectPermanentTargetSpec",
) -> set[str]:
    return set(spec.types_any or spec.types_all)


def _validate_direct_type_predicates(
    spec: "DirectPermanentTargetSpec",
) -> None:
    if spec.types_any and spec.types_all:
        raise ValueError(
            "Direct permanent targets require one positive type predicate"
        )
    if spec.types_any and (
        len(spec.types_any) > 4
        or not set(spec.types_any).issubset(_DIRECT_TYPE_ANY_VALUES)
    ):
        raise ValueError(
            "Direct permanent target type disjunction is unsupported"
        )
    if spec.types_all and (
        len(spec.types_all) > 2
        or not set(spec.types_all).issubset(_DIRECT_TYPE_ANY_VALUES)
    ):
        raise ValueError("Direct permanent target type conjunction is unsupported")
    if spec.types_none and (
        len(spec.types_none) > 2
        or not set(spec.types_none).issubset(_DIRECT_TYPE_ANY_VALUES)
    ):
        raise ValueError(
            "Direct permanent excluded type predicate is unsupported"
        )
    if _positive_direct_types(spec).intersection(spec.types_none):
        raise ValueError("Direct permanent type predicates contradict each other")


def _validate_direct_subtype_predicates(
    spec: "DirectPermanentTargetSpec",
) -> None:
    if spec.subtypes_any:
        if (
            spec.types_any
            or spec.types_all
            or spec.types_none
            or len(spec.subtypes_any) > 8
        ):
            raise ValueError(
                "Direct permanent subtype targets require one closed disjunction"
            )
        for subtype in spec.subtypes_any:
            if (
                canonical_creature_subtype(subtype) != subtype
                and subtype not in DIRECT_NONCREATURE_SUBTYPES
            ):
                raise ValueError(
                    f"Direct permanent target subtype {subtype!r} is unsupported"
                )
    if spec.subtypes_none:
        if (
            "creature" not in _positive_direct_types(spec)
            or len(spec.subtypes_none) > 8
        ):
            raise ValueError(
                "Direct permanent excluded subtypes require one closed creature predicate"
            )
        for subtype in spec.subtypes_none:
            if canonical_creature_subtype(subtype) != subtype:
                raise ValueError(
                    f"Direct permanent excluded subtype {subtype!r} is unsupported"
                )


def _validate_direct_supertype_predicates(
    spec: "DirectPermanentTargetSpec",
) -> None:
    if not (spec.supertypes_any or spec.supertypes_none):
        return
    if (
        len(spec.supertypes_any) > 1
        or len(spec.supertypes_none) > 1
        or not set(spec.supertypes_any).issubset(_DIRECT_SUPERTYPES)
        or not set(spec.supertypes_none).issubset(_DIRECT_SUPERTYPES)
        or set(spec.supertypes_any).intersection(spec.supertypes_none)
    ):
        raise ValueError("Direct permanent supertype predicate is unsupported")


def _validate_direct_keyword_predicates(
    spec: "DirectPermanentTargetSpec",
) -> None:
    if not (spec.keywords_all or spec.keywords_none):
        return
    has_creature_type = spec.types_any == ("creature",) or (
        "creature" in spec.types_all and not spec.types_any
    )
    if (
        not has_creature_type
        or len(spec.keywords_all) > 1
        or len(spec.keywords_none) > 1
        or not set(spec.keywords_all).issubset(_DIRECT_KEYWORDS)
        or not set(spec.keywords_none).issubset(_DIRECT_KEYWORDS)
        or set(spec.keywords_all).intersection(spec.keywords_none)
    ):
        raise ValueError(
            "Direct permanent keyword predicate requires one closed creature quality"
        )


def _validate_direct_color_predicates(
    spec: "DirectPermanentTargetSpec",
) -> None:
    if len(spec.colors_any) > 2 or len(spec.colors_none) > 1:
        raise ValueError("Direct permanent color predicate is unsupported")
    color_predicate_count = sum(
        bool(value)
        for value in (
            spec.colors_any,
            spec.colors_none,
            spec.colorless is not None,
            spec.color_count_min is not None,
            spec.color_count_equal is not None,
        )
    )
    if color_predicate_count > 1:
        raise ValueError("Direct permanent color predicates are mutually exclusive")
    if spec.colorless is not None and spec.colorless is not True:
        raise ValueError(
            "Direct permanent colorless predicates require the closed positive form"
        )
    for field_name in ("color_count_min", "color_count_equal"):
        value = getattr(spec, field_name)
        if value is not None and (
            type(value) is not int or not 1 <= value <= 5
        ):
            raise ValueError(
                "Direct permanent color-count predicate is unsupported"
            )


def _validate_direct_target_flags(
    spec: "DirectPermanentTargetSpec",
) -> None:
    if spec.token is not None and (
        spec.token is not True
        or "creature" not in _positive_direct_types(spec)
    ):
        raise ValueError(
            "Direct permanent token predicates require a creature target"
        )
    if spec.controller_relation not in {"any", "you", "opponent"}:
        raise ValueError("Direct permanent target controller relation is unsupported")
    if type(spec.source_exclusion) is not bool:
        raise ValueError("Direct permanent target source exclusion must be boolean")
    if spec.commander is not None and (
        spec.commander is not True or spec.types_any != ("creature",)
    ):
        raise ValueError(
            "Direct permanent commander targets require a creature predicate"
        )


@dataclass(frozen=True, slots=True)
class DirectPermanentTargetSpec:
    """One closed, immutable direct-permanent target predicate.

    This is a compiler-owned semantic value.  Runtime target schemas are a
    deterministic serialization of it rather than a second Oracle-text
    interpretation.
    """

    types_any: tuple[str, ...] = ()
    types_all: tuple[str, ...] = ()
    types_none: tuple[str, ...] = ()
    subtypes_any: tuple[str, ...] = ()
    subtypes_none: tuple[str, ...] = ()
    supertypes_any: tuple[str, ...] = ()
    supertypes_none: tuple[str, ...] = ()
    keywords_all: tuple[str, ...] = ()
    keywords_none: tuple[str, ...] = ()
    colors_any: tuple[str, ...] = ()
    colors_none: tuple[str, ...] = ()
    colorless: bool | None = None
    color_count_min: int | None = None
    color_count_equal: int | None = None
    mana_value_min: int | None = None
    mana_value_max: int | None = None
    mana_value_equal: int | None = None
    state_predicate: PermanentStatePredicateSpec | None = None
    controller_relation: str = "any"
    source_exclusion: bool = False
    token: bool | None = None
    commander: bool | None = None

    def __post_init__(self) -> None:
        _canonicalize_direct_target_spec(self)
        _validate_direct_type_predicates(self)
        _validate_direct_subtype_predicates(self)
        _validate_direct_supertype_predicates(self)
        _validate_direct_keyword_predicates(self)
        _validate_direct_color_predicates(self)
        _validate_mana_value_predicate(self)
        _validate_public_state_predicate(self)
        _validate_direct_target_flags(self)

    @property
    def characteristic_slug(self) -> str:
        """Return the canonical characteristic-only predicate identity."""

        if self.types_none == ("land",) and not (
            self.types_any or self.types_all
        ):
            predicate = "nonland-permanent"
        elif self.types_any:
            predicate = "-or-".join(self.types_any)
        elif self.types_all:
            predicate = "-".join(self.types_all)
        elif self.subtypes_any:
            predicate = "-or-".join(self.subtypes_any)
        else:
            predicate = "permanent"
        if self.types_none and not (
            self.types_none == ("land",)
            and not (self.types_any or self.types_all)
        ):
            predicate += "-non-" + "-and-".join(self.types_none)
        if self.supertypes_any:
            predicate = "-and-".join(self.supertypes_any) + "-" + predicate
        if self.supertypes_none:
            predicate = (
                "non-"
                + "-and-".join(self.supertypes_none)
                + "-"
                + predicate
            )
        if self.colors_any:
            predicate = (
                "-or-".join(value.casefold() for value in self.colors_any)
                + "-"
                + predicate
            )
        if self.keywords_all:
            predicate += "-with-" + "-and-".join(self.keywords_all)
        if self.keywords_none:
            predicate += "-without-" + "-and-".join(self.keywords_none)
        if self.subtypes_none:
            predicate += "-non-" + "-and-".join(self.subtypes_none)
        if self.colors_none:
            predicate += "-non-" + "-and-".join(
                value.casefold() for value in self.colors_none
            )
        if self.colorless:
            predicate += "-colorless"
        if self.color_count_equal is not None:
            predicate += f"-exactly-{self.color_count_equal}-colors"
        elif self.color_count_min is not None:
            predicate += f"-at-least-{self.color_count_min}-colors"
        if self.token:
            predicate += "-token"
        if self.commander:
            predicate = f"commander-{predicate}"
        if self.mana_value_equal is not None:
            predicate += f"-mana-value-{self.mana_value_equal}"
        elif self.mana_value_min is not None:
            predicate += f"-mana-value-{self.mana_value_min}-or-greater"
        elif self.mana_value_max is not None:
            predicate += f"-mana-value-{self.mana_value_max}-or-less"
        return predicate

    @property
    def slug(self) -> str:
        predicate = self.characteristic_slug
        if self.controller_relation != "any":
            predicate += f"-{self.controller_relation}"
        if self.source_exclusion:
            predicate += "-another"
        if self.state_predicate is not None:
            state = self.state_predicate
            if state.entered_this_turn:
                predicate += "-entered-this-turn"
            elif state.tapped is not None:
                predicate += "-tapped" if state.tapped else "-untapped"
            else:
                assert state.counter_name is not None
                predicate += "-with-" + direct_target_slug(
                    state.counter_name
                ) + "-counter"
        return predicate

    @property
    def uses_compound_characteristics(self) -> bool:
        """Whether this spec exceeds the historical single type/subtype grammar."""

        return bool(
            self.types_all
            or self.types_none
            or self.supertypes_any
            or self.supertypes_none
            or self.keywords_all
            or self.keywords_none
            or len(self.types_any) > 1
            or len(self.subtypes_any) > 1
            or self.subtypes_none
            or self.colors_any
            or self.colors_none
            or self.colorless is not None
            or self.color_count_min is not None
            or self.color_count_equal is not None
            or self.token is not None
            or self.mana_value_min is not None
            or self.mana_value_max is not None
            or self.mana_value_equal is not None
        )

    @property
    def uses_public_state(self) -> bool:
        return self.state_predicate is not None

    def to_target_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "count": 1,
        }
        for field_name in (
            "types_any",
            "types_all",
            "types_none",
            "subtypes_any",
            "subtypes_none",
            "supertypes_any",
            "supertypes_none",
            "keywords_all",
            "keywords_none",
            "colors_any",
            "colors_none",
        ):
            values = getattr(self, field_name)
            if values:
                schema[field_name] = list(values)
        if self.colorless is not None:
            schema["colorless"] = self.colorless
        if self.color_count_min is not None:
            schema["color_count_min"] = self.color_count_min
        if self.color_count_equal is not None:
            schema["color_count_equal"] = self.color_count_equal
        if self.mana_value_min is not None:
            schema["mana_value_min"] = self.mana_value_min
        if self.mana_value_max is not None:
            schema["mana_value_max"] = self.mana_value_max
        if self.mana_value_equal is not None:
            schema["mana_value"] = self.mana_value_equal
        if self.state_predicate is not None:
            schema["state_predicate"] = self.state_predicate.to_dict()
        if self.controller_relation != "any":
            schema["controller_relation"] = self.controller_relation
        if self.source_exclusion:
            schema["source_exclusion"] = True
        if self.token is not None:
            schema["token"] = self.token
        if self.commander is not None:
            schema["commander"] = self.commander
        return schema

    @classmethod
    def from_target_schema(
        cls,
        value: Mapping[str, Any],
        *,
        allow_commander: bool = False,
    ) -> "DirectPermanentTargetSpec":
        if not isinstance(value, Mapping):
            raise ValueError("Direct permanent target schema must be an object")
        schema = dict(value)
        allowed = {
            "zones",
            "categories",
            "count",
            "types_any",
            "types_all",
            "types_none",
            "subtypes_any",
            "subtypes_none",
            "supertypes_any",
            "supertypes_none",
            "keywords_all",
            "keywords_none",
            "colors_any",
            "colors_none",
            "colorless",
            "color_count_min",
            "color_count_equal",
            "mana_value_min",
            "mana_value_max",
            "mana_value",
            "state_predicate",
            "controller_relation",
            "source_exclusion",
            "token",
            *(('commander',) if allow_commander else ()),
        }
        if set(schema) - allowed:
            raise ValueError("Direct permanent target schema has unknown fields")
        if (
            schema.get("zones") != ["battlefield"]
            or schema.get("categories") != ["permanent"]
            or type(schema.get("count")) is not int
            or schema.get("count") != 1
        ):
            raise ValueError("Direct permanent target schema header is unsupported")
        source_exclusion = schema.get("source_exclusion", False)
        if type(source_exclusion) is not bool:
            raise ValueError("Direct permanent target source exclusion must be boolean")
        raw_state = schema.get("state_predicate")
        try:
            state_predicate = (
                PermanentStatePredicateSpec.from_dict(raw_state)
                if raw_state is not None
                else None
            )
        except ObjectQueryError as exc:
            raise ValueError(str(exc)) from exc
        spec = cls(
            types_any=tuple(schema.get("types_any", ())),
            types_all=tuple(schema.get("types_all", ())),
            types_none=tuple(schema.get("types_none", ())),
            subtypes_any=tuple(schema.get("subtypes_any", ())),
            subtypes_none=tuple(schema.get("subtypes_none", ())),
            supertypes_any=tuple(schema.get("supertypes_any", ())),
            supertypes_none=tuple(schema.get("supertypes_none", ())),
            keywords_all=tuple(schema.get("keywords_all", ())),
            keywords_none=tuple(schema.get("keywords_none", ())),
            colors_any=tuple(schema.get("colors_any", ())),
            colors_none=tuple(schema.get("colors_none", ())),
            colorless=schema.get("colorless"),
            color_count_min=schema.get("color_count_min"),
            color_count_equal=schema.get("color_count_equal"),
            mana_value_min=schema.get("mana_value_min"),
            mana_value_max=schema.get("mana_value_max"),
            mana_value_equal=schema.get("mana_value"),
            state_predicate=state_predicate,
            controller_relation=schema.get("controller_relation", "any"),
            source_exclusion=source_exclusion,
            token=schema.get("token"),
            commander=schema.get("commander"),
        )
        if spec.to_target_schema() != schema:
            raise ValueError("Direct permanent target schema is not canonical")
        return spec


def direct_permanent_target_spec(
    subject: str,
) -> DirectPermanentTargetSpec | None:
    """Parse one closed direct-permanent target predicate.

    Effect-family compilers share this grammar so counter placement, counter
    removal, and other direct-target clauses cannot disagree about the same
    Oracle subject.
    """

    if type(subject) is not str:
        return None
    phrase = " ".join(subject.casefold().split())
    exclude_source = phrase.startswith("another target ")
    if exclude_source:
        phrase = phrase[len("another target ") :]
    elif phrase.startswith("target "):
        phrase = phrase[len("target ") :]
    else:
        return None

    state_predicate: PermanentStatePredicateSpec | None = None
    phrase, mana_value_fields = _strip_mana_value_predicate(phrase)
    counter_state = re.fullmatch(
        r"(?P<body>.+) with (?:a|an) "
        r"(?P<counter>[+-]\d+/[+-]\d+|[a-z][a-z'-]*(?: [a-z][a-z'-]*){0,2}) "
        r"counter on it",
        phrase,
    )
    if counter_state is not None:
        phrase = counter_state.group("body")
        try:
            state_predicate = PermanentStatePredicateSpec(
                counter_name=counter_state.group("counter"),
                minimum_counter_count=1,
            )
        except ObjectQueryError:
            return None
    else:
        for suffix in (
            " that entered the battlefield this turn",
            " that entered this turn",
        ):
            if phrase.endswith(suffix):
                phrase = phrase[: -len(suffix)]
                state_predicate = PermanentStatePredicateSpec(
                    entered_this_turn=True
                )
                break

    relation = "any"
    for suffix, candidate in (
        (" an opponent controls", "opponent"),
        (" you don't control", "opponent"),
        (" you control", "you"),
    ):
        if phrase.endswith(suffix):
            phrase = phrase[: -len(suffix)]
            relation = candidate
            break

    kwargs: dict[str, Any] = {
        "controller_relation": relation,
        "source_exclusion": exclude_source,
        "state_predicate": state_predicate,
        **mana_value_fields,
    }
    if (
        counter_state is not None
        and counter_state.group("counter") == "counter"
    ):
        return None

    state_match = re.fullmatch(
        r"(?P<state>tapped|untapped) (?P<body>.+)",
        phrase,
    )
    if state_match is not None:
        if state_predicate is not None:
            return None
        phrase = state_match.group("body")
        kwargs["state_predicate"] = PermanentStatePredicateSpec(
            tapped=state_match.group("state") == "tapped"
        )

    characteristic_fields = _closed_direct_characteristic_fields(phrase)
    if characteristic_fields is not None:
        if (
            characteristic_fields.get("types_any") == ("creature",)
            and (
                characteristic_fields.get("keywords_all")
                or characteristic_fields.get("keywords_none")
            )
        ):
            characteristic_fields = dict(characteristic_fields)
            characteristic_fields.pop("types_any")
            characteristic_fields["types_all"] = ("creature",)
        kwargs.update(characteristic_fields)
    elif phrase.endswith(" creature") and all(
        value.startswith("non-")
        for value in _split_direct_or_terms(phrase[: -len(" creature")])
    ):
        raw_subtypes = tuple(
            value[len("non-") :].strip()
            for value in _split_direct_or_terms(phrase[: -len(" creature")])
            if value.strip()
        )
        subtypes = tuple(
            canonical_creature_subtype(value) for value in raw_subtypes
        )
        if not subtypes or any(value is None for value in subtypes):
            return None
        kwargs["types_any"] = ("creature",)
        kwargs["subtypes_none"] = tuple(
            value for value in subtypes if value is not None
        )
    else:
        explicit_creature = phrase.endswith(" creature")
        if explicit_creature:
            phrase = phrase[: -len(" creature")]
        raw_terms = _split_direct_or_terms(phrase)
        if not raw_terms:
            return None
        subtypes: list[str] = []
        for value in raw_terms:
            subtype = canonical_creature_subtype(value)
            if subtype is None and value not in DIRECT_NONCREATURE_SUBTYPES:
                return None
            subtypes.append(subtype or value)
        kwargs["subtypes_any"] = tuple(subtypes)
    try:
        return DirectPermanentTargetSpec(**kwargs)
    except ValueError:
        return None


def _closed_values(
    values: Sequence[str],
    *,
    field: str,
    required: bool = False,
) -> list[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"Direct-target {field} must be an array")
    normalized = list(values)
    if required and not normalized:
        raise ValueError(f"Direct-target {field} must not be empty")
    if any(type(value) is not str or not value for value in normalized):
        raise ValueError(
            f"Direct-target {field} values must be nonempty strings"
        )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"Direct-target {field} values must be unique")
    return normalized


def direct_target_slug(value: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("Direct-target slugs require a nonempty value")
    return (
        value.casefold().replace(",", "").replace(" or ", "-or-").replace(" ", "-")
    )


def direct_target_effect(
    operation: str,
    *,
    reference_field: str,
) -> tuple[Mapping[str, Any], ...]:
    if type(operation) is not str or not operation:
        raise ValueError("Direct-target operations must be nonempty")
    if type(reference_field) is not str or not reference_field:
        raise ValueError("Direct-target reference fields must be nonempty")
    return ({"op": operation, reference_field: "$target.0"},)


def permanent_target_schema(
    *,
    types_any: Sequence[str] = (),
    types_none: Sequence[str] = (),
) -> Mapping[str, Any]:
    any_values = _closed_values(types_any, field="types_any")
    none_values = _closed_values(types_none, field="types_none")
    if types_any and types_none:
        raise ValueError("Direct permanent targets require one type predicate")
    schema: dict[str, Any] = {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "count": 1,
    }
    if any_values:
        schema["types_any"] = any_values
    if none_values:
        schema["types_none"] = none_values
    return schema


def stack_target_schema(
    *,
    categories: Sequence[str],
    types_any: Sequence[str] = (),
    types_none: Sequence[str] = (),
    colors_any: Sequence[str] = (),
    predicate: str | None = None,
    colorless: bool | None = None,
) -> Mapping[str, Any]:
    category_values = _closed_values(
        categories,
        field="categories",
        required=True,
    )
    any_values = _closed_values(types_any, field="types_any")
    none_values = _closed_values(types_none, field="types_none")
    color_values = _closed_values(colors_any, field="colors_any")
    predicates = sum(
        bool(value)
        for value in (
            any_values,
            none_values,
            color_values,
            predicate,
            colorless,
        )
    )
    if predicates > 1:
        raise ValueError("Direct stack targets require one optional predicate")
    schema: dict[str, Any] = {
        "zones": ["stack"],
        "categories": category_values,
        "source_exclusion": True,
        "count": 1,
    }
    if any_values:
        schema["types_any"] = any_values
    elif none_values:
        schema["types_none"] = none_values
    elif color_values:
        schema["colors_any"] = color_values
    elif predicate is not None:
        if type(predicate) is not str or not predicate:
            raise ValueError("Direct stack predicates must be nonempty")
        schema["predicate"] = predicate
    elif colorless is not None:
        if type(colorless) is not bool:
            raise ValueError("Direct stack colorless predicates must be boolean")
        schema["colorless"] = colorless
    return schema


def compiled_direct_target(
    *,
    template_id: str,
    effects: tuple[Mapping[str, Any], ...],
    target_schema: Mapping[str, Any],
    mechanics: tuple[str, ...],
) -> CompiledDirectTarget:
    if type(template_id) is not str or not template_id:
        raise ValueError("Direct-target templates require an identity")
    if len(effects) != 1 or not isinstance(effects[0], Mapping):
        raise ValueError("Direct-target templates require one effect")
    if not isinstance(target_schema, Mapping):
        raise ValueError("Direct-target templates require a target schema")
    mechanic_values = _closed_values(
        mechanics,
        field="mechanics",
        required=True,
    )
    return template_id, effects, target_schema, tuple(mechanic_values)


__all__ = [
    "CompiledDirectTarget",
    "DIRECT_NONCREATURE_SUBTYPES",
    "DIRECT_PERMANENT_TYPES",
    "DirectPermanentTargetSpec",
    "compiled_direct_target",
    "direct_permanent_target_spec",
    "direct_target_effect",
    "direct_target_slug",
    "permanent_target_schema",
    "stack_target_schema",
]
