from __future__ import annotations

"""Closed private hand-to-battlefield choice grammar."""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..util import stable_json


FIXED_PRIVATE_HAND_ENTRY_CAPABILITY = "zone.move.fixed_private_hand_choice"
FIXED_PRIVATE_HAND_ENTRY_MECHANIC = "fixed-private-hand-entry"

_PERMANENT_TYPES = (
    "artifact",
    "battle",
    "creature",
    "enchantment",
    "land",
    "planeswalker",
)
_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}


def _hand_entry_query(subject: str) -> ObjectQuerySpec | None:
    normalized = " ".join(subject.casefold().split())
    fields: dict[str, Any] = {"zones": ("hand",)}
    if normalized == "land":
        fields["types_all"] = ("land",)
    elif normalized == "basic land":
        fields.update(
            types_all=("land",),
            supertypes_all=("basic",),
        )
    elif normalized == "creature":
        fields["types_all"] = ("creature",)
    elif normalized == "equipment":
        fields.update(
            types_all=("artifact",),
            subtypes_all=("equipment",),
        )
    elif normalized == "multicolored creature":
        fields.update(
            types_all=("creature",),
            minimum_color_count=2,
        )
    else:
        colored = re.fullmatch(
            r"(?P<colors>white|blue|black|red|green)"
            r"(?: or (?P<second>white|blue|black|red|green))? creature",
            normalized,
        )
        if colored is not None:
            values = tuple(
                sorted(
                    {
                        _COLORS[colored.group("colors")],
                        *(
                            (_COLORS[colored.group("second")],)
                            if colored.group("second")
                            else ()
                        ),
                    }
                )
            )
            fields.update(types_all=("creature",), colors_any=values)
        elif normalized == "minotaur permanent":
            fields.update(
                types_any=_PERMANENT_TYPES,
                subtypes_all=("minotaur",),
            )
        else:
            return None
    return ObjectQuerySpec(**fields)


@dataclass(frozen=True, slots=True)
class FixedPrivateHandEntryTemplate:
    query: ObjectQuerySpec
    tapped: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.query, ObjectQuerySpec) or self.query.zones != (
            "hand",
        ):
            raise ValueError("Private hand entry requires a typed hand query")
        if type(self.tapped) is not bool:
            raise ValueError("Private hand entry tapped policy must be boolean")

    @property
    def template_id(self) -> str:
        identity = {
            "query": self.query.canonical_dict(),
            "tapped": self.tapped,
        }
        digest = hashlib.sha256(stable_json(identity).encode("utf-8")).hexdigest()
        return f"put-private-hand-card-onto-battlefield-{digest[:16]}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "put_card_from_hand",
                "player": "$controller",
                "query": self.query.canonical_dict(),
                "tapped": self.tapped,
            },
        )

    @property
    def target_schema(self) -> None:
        return None

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            FIXED_PRIVATE_HAND_ENTRY_MECHANIC,
            "fixed-public-zone-move",
        )

    def compiled(self):
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def fixed_private_hand_entry_effect_template(
    text: str,
) -> FixedPrivateHandEntryTemplate | None:
    match = re.fullmatch(
        r"You may put (?:a|an) (?P<subject>.+?) card from your hand onto "
        r"the battlefield(?P<tapped> tapped)?\.?",
        " ".join(text.strip().split()),
        re.IGNORECASE,
    )
    if match is None:
        return None
    query = _hand_entry_query(match.group("subject"))
    return (
        FixedPrivateHandEntryTemplate(
            query=query,
            tapped=bool(match.group("tapped")),
        )
        if query is not None
        else None
    )


__all__ = [
    "FIXED_PRIVATE_HAND_ENTRY_CAPABILITY",
    "FIXED_PRIVATE_HAND_ENTRY_MECHANIC",
    "FixedPrivateHandEntryTemplate",
    "fixed_private_hand_entry_effect_template",
]
