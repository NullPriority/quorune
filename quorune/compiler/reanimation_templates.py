from __future__ import annotations

"""Closed single-target graveyard-to-battlefield lowering."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..effect_contracts import REANIMATE_OPERATION
from ..targets import TargetGroup


FIXED_TARGET_REANIMATION_CAPABILITY = "zone.reanimate.fixed_target"
FIXED_TARGET_REANIMATION_MECHANIC = "fixed-target-reanimation"

_PERMANENT_TYPES = (
    "artifact",
    "battle",
    "creature",
    "enchantment",
    "land",
    "planeswalker",
)
_REANIMATION = re.compile(
    r"^(?:[•]\s*)?(?P<verb>Return|Put) (?P<optional>up to one )?target "
    r"(?P<quality>.+?) card"
    r"(?: with (?P<numeric>mana value|power) "
    r"(?P<numeric_value>[1-9][0-9]*) or less)? from "
    r"(?P<origin>your|a|any|an opponent['’]s) graveyard "
    r"(?P<direction>to|onto) the battlefield"
    r"(?P<tapped> tapped)?"
    r"(?: under (?P<controller>your|its owner['’]s) control)?\.?$",
    re.IGNORECASE,
)


def _quality_fields(value: str) -> dict[str, Any] | None:
    text = " ".join(value.casefold().split())
    fields = {
        "creature": {"types_any": ["creature"]},
        "artifact": {"types_any": ["artifact"]},
        "enchantment": {"types_any": ["enchantment"]},
        "land": {"types_any": ["land"]},
        "planeswalker": {"types_any": ["planeswalker"]},
        "permanent": {"types_any": list(_PERMANENT_TYPES)},
        "nonland permanent": {
            "types_any": [
                value for value in _PERMANENT_TYPES if value != "land"
            ],
            "types_none": ["land"],
        },
        "artifact or creature": {"types_any": ["artifact", "creature"]},
        "creature or enchantment": {
            "types_any": ["creature", "enchantment"]
        },
        "creature or planeswalker": {
            "types_any": ["creature", "planeswalker"]
        },
        "vehicle": {
            "types_any": ["artifact"],
            "subtypes_any": ["vehicle"],
        },
        "cave": {
            "types_any": ["land"],
            "subtypes_any": ["cave"],
        },
        "creature or vehicle": {
            "types_any": ["artifact", "creature"],
            "characteristic_forms_any": [
                {
                    "types_all": ["creature"],
                    "subtypes_any": [],
                    "supertypes_any": [],
                },
                {
                    "types_all": [],
                    "subtypes_any": ["vehicle"],
                    "supertypes_any": [],
                },
            ],
        },
        "creature or spacecraft": {
            "types_any": ["artifact", "creature"],
            "characteristic_forms_any": [
                {
                    "types_all": ["creature"],
                    "subtypes_any": [],
                    "supertypes_any": [],
                },
                {
                    "types_all": [],
                    "subtypes_any": ["spacecraft"],
                    "supertypes_any": [],
                },
            ],
        },
        "artifact, enchantment, or planeswalker": {
            "types_any": ["artifact", "enchantment", "planeswalker"]
        },
        "legendary creature": {
            "types_any": ["creature"],
            "supertypes_any": ["legendary"],
        },
    }.get(text)
    if fields is not None:
        return fields
    subtype = re.fullmatch(
        r"(?P<subtype>[A-Za-z][A-Za-z'’-]*) creature",
        value.strip(),
    )
    if subtype is None:
        return None
    canonical = canonical_creature_subtype(subtype.group("subtype"))
    if canonical is None:
        return None
    return {
        "types_any": ["creature"],
        "subtypes_any": [canonical],
    }


@dataclass(frozen=True, slots=True)
class FixedTargetReanimationTemplate:
    target_schema: Mapping[str, Any]
    tapped: bool
    owner_controls: bool

    def __post_init__(self) -> None:
        schema = dict(self.target_schema)
        group = TargetGroup.from_mapping(schema)
        if (
            group.zones != ("graveyard",)
            or group.categories != ("card",)
            or group.controller_relation != "any"
            or group.owner_relation not in {"any", "you", "opponent"}
            or group.min_targets not in {0, 1}
            or group.max_targets != 1
            or not set((*group.types_any, *group.types_all)).intersection(
                _PERMANENT_TYPES
            )
            or set(group.types_none) - {"land"}
        ):
            raise ValueError("Reanimation target schema is outside the closed family")
        if type(self.tapped) is not bool or type(self.owner_controls) is not bool:
            raise ValueError("Reanimation result flags must be booleans")
        object.__setattr__(self, "target_schema", schema)

    @property
    def template_id(self) -> str:
        relation = str(self.target_schema.get("owner_relation") or "any")
        optional = "optional-" if "min" in self.target_schema else ""
        tapped = "-tapped" if self.tapped else ""
        controller = "-owner-control" if self.owner_controls else ""
        return (
            f"reanimate-{optional}target-{relation}-graveyard-permanent"
            f"{tapped}{controller}-v1"
        )

    def compiled(self) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            (
                {
                    "op": REANIMATE_OPERATION,
                    "card": "$target.0",
                    "controller": (
                        "$target.owner.0" if self.owner_controls else "$controller"
                    ),
                    "tapped": self.tapped,
                },
            ),
            dict(self.target_schema),
            (FIXED_TARGET_REANIMATION_MECHANIC, "cr-115-targets"),
        )


def fixed_target_reanimation_effect_template(
    text: str,
) -> FixedTargetReanimationTemplate | None:
    """Parse one target permanent-card move from a public graveyard."""

    match = _REANIMATION.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    if (
        match.group("verb").casefold() == "return"
        and match.group("direction").casefold() != "to"
    ) or (
        match.group("verb").casefold() == "put"
        and match.group("direction").casefold() != "onto"
    ):
        return None
    if match.group("verb").casefold() == "put" and match.group("controller") is None:
        return None
    quality = _quality_fields(match.group("quality"))
    if quality is None:
        return None
    origin = match.group("origin").casefold().replace("’", "'")
    target_schema: dict[str, Any] = {
        "zones": ["graveyard"],
        "categories": ["card"],
        "owner_relation": (
            "you"
            if origin == "your"
            else "opponent"
            if origin == "an opponent's"
            else "any"
        ),
        **quality,
        **(
            {"min": 0, "max": 1}
            if match.group("optional")
            else {"count": 1}
        ),
    }
    numeric = (match.group("numeric") or "").casefold()
    if numeric == "mana value":
        target_schema["mana_value_max"] = int(match.group("numeric_value"))
    elif numeric == "power":
        if quality.get("types_any") != ["creature"]:
            return None
        target_schema["numeric_characteristic"] = {
            "characteristic": "power",
            "comparison": "at_most",
            "value": int(match.group("numeric_value")),
        }
    controller = (match.group("controller") or "").casefold().replace("’", "'")
    return FixedTargetReanimationTemplate(
        target_schema=target_schema,
        tapped=bool(match.group("tapped")),
        owner_controls=controller == "its owner's",
    )


__all__ = [
    "FIXED_TARGET_REANIMATION_CAPABILITY",
    "FIXED_TARGET_REANIMATION_MECHANIC",
    "FixedTargetReanimationTemplate",
    "fixed_target_reanimation_effect_template",
]
