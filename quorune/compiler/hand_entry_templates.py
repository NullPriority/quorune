from __future__ import annotations

"""Closed private hand-to-battlefield choice grammar."""

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..hand_entry_queries import decode_hand_entry_queries, historic_hand_entry_query_descriptor
from ..replacement.immutable import FrozenMap, thaw_value
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
    elif normalized == "artifact":
        fields["types_all"] = ("artifact",)
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
    query: ObjectQuerySpec | Mapping[str, Any]
    tapped: bool = False

    def __post_init__(self) -> None:
        value = self.query.canonical_dict() if isinstance(self.query, ObjectQuerySpec) else self.query
        if not decode_hand_entry_queries(value):
            raise ValueError("Private hand entry requires a typed hand query")
        if type(self.tapped) is not bool:
            raise ValueError("Private hand entry tapped policy must be boolean")
        if not isinstance(self.query, ObjectQuerySpec):
            object.__setattr__(self, 'query', FrozenMap(value))

    @property
    def template_id(self) -> str:
        identity = {
            "query": self.query.canonical_dict() if isinstance(self.query,ObjectQuerySpec) else thaw_value(self.query),
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
                "query": self.query.canonical_dict() if isinstance(self.query,ObjectQuerySpec) else thaw_value(self.query),
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
    query = (
        historic_hand_entry_query_descriptor()
        if match.group('subject').casefold() == 'historic permanent'
        else _hand_entry_query(match.group("subject"))
    )
    return (
        FixedPrivateHandEntryTemplate(
            query=query,
            tapped=bool(match.group("tapped")),
        )
        if query is not None
        else None
    )


def fixed_draw_then_hand_entry_template(text: str):
    from .draw_templates import fixed_draw_effect_template
    match = re.fullmatch(
        r'(?P<draw>(?:You )?draw [^.]+?)(?:, then|\.)(?: then)? '
        r'(?P<entry>you may put .+)\.', text.strip(), re.IGNORECASE,
    )
    if match is None:
        return None
    draw = fixed_draw_effect_template(match['draw'] + '.')
    entry = fixed_private_hand_entry_effect_template(match['entry'] + '.')
    if (
        draw is None or entry is None or draw[2] is not None
        or len(draw[1]) != 1 or draw[1][0].get('op') != 'draw'
    ):
        return None
    return (
        'fixed-draw-then-private-hand-entry-v1', (*draw[1], *entry.effects), None,
        tuple(dict.fromkeys(('closed-effect-program', *draw[3], *entry.mechanics))),
    )


__all__ = [
    "FIXED_PRIVATE_HAND_ENTRY_CAPABILITY",
    "FIXED_PRIVATE_HAND_ENTRY_MECHANIC",
    "FixedPrivateHandEntryTemplate",
    "fixed_private_hand_entry_effect_template",
]
