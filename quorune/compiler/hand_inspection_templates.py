from __future__ import annotations

"""Closed lowering for target-hand inspection and immediate card movement."""

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..rules.hand_inspection import (
    FIXED_HAND_INSPECTION_OPERATION,
    HandCardPredicateSpec,
    HandInspectionAction,
    HandInspectionMode,
)
from ..object_predicate import ObjectQuerySpec
from .life_templates import fixed_life_effect_template
from .scry_templates import fixed_scry_effect_template


FIXED_HAND_INSPECTION_MECHANIC = "fixed-target-hand-inspection"
FIXED_HAND_INSPECTION_CAPABILITY = "choice.target_hand.fixed_inspection_move"
_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "kindred",
        "land",
        "planeswalker",
        "sorcery",
    }
)
_PERMANENT_TYPES = _CARD_TYPES - {"instant", "kindred", "sorcery"}
_KNOWN_SUBTYPES = frozenset({"arcane", "spirit", "trap"})
_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}


def _terms(value: str) -> tuple[str, ...]:
    return tuple(
        term.strip()
        for term in re.split(r",\s*(?:or\s+)?|\s+or\s+", value)
        if term.strip()
    )


def _quality_predicate(value: str) -> HandCardPredicateSpec | None:
    normalized = " ".join(value.casefold().split())
    if any(character in normalized for character in "[]{}\""):
        return None
    mana_value_min = None
    mana_value_max = None
    mana_match = re.fullmatch(
        r"(?P<body>.+?) with mana value (?P<value>\d+) or "
        r"(?P<direction>less|greater)",
        normalized,
    )
    if mana_match is not None:
        normalized = mana_match.group("body")
        value_number = int(mana_match.group("value"))
        if mana_match.group("direction") == "less":
            mana_value_max = value_number
        else:
            mana_value_min = value_number

    fields: dict[str, Any] = {"zones": ("hand",)}
    excluded_supertypes: tuple[str, ...] = ()
    if normalized == "a card":
        pass
    else:
        article_match = re.fullmatch(r"(?:a|an) (?P<body>.+?) card", normalized)
        if article_match is None:
            return None
        quality = article_match.group("body")
        if quality == "nonland":
            fields["excluded_types"] = ("land",)
        elif quality == "noncreature":
            fields["excluded_types"] = ("creature",)
        elif quality == "noncreature, nonland":
            fields["excluded_types"] = ("creature", "land")
        elif quality == "nonlegendary, nonland":
            fields["excluded_types"] = ("land",)
            excluded_supertypes = ("legendary",)
        elif quality == "nonbasic land":
            fields["types_all"] = ("land",)
            excluded_supertypes = ("basic",)
        elif quality == "nonland permanent":
            fields["types_any"] = tuple(sorted(_PERMANENT_TYPES - {"land"}))
            fields["excluded_types"] = ("land",)
        elif quality in _CARD_TYPES:
            fields["types_all"] = (quality,)
        else:
            colored = re.fullmatch(
                r"(?P<colors>white|blue|black|red|green)(?: or "
                r"(?P<second>white|blue|black|red|green))? "
                r"(?P<card_type>artifact|battle|creature|enchantment|instant|"
                r"kindred|land|planeswalker|sorcery)",
                quality,
            )
            if colored is not None:
                colors = tuple(
                    _COLORS[value]
                    for value in (
                        colored.group("colors"),
                        colored.group("second"),
                    )
                    if value
                )
                fields["types_all"] = (colored.group("card_type"),)
                fields["colors_any"] = colors
            else:
                terms = _terms(quality)
                if len(terms) == 1 and terms[0] in _KNOWN_SUBTYPES:
                    fields["subtypes_any"] = terms
                elif len(terms) >= 2 and set(terms).issubset(_CARD_TYPES):
                    fields["types_any"] = terms
                elif len(terms) >= 2 and set(terms).issubset(_KNOWN_SUBTYPES):
                    fields["subtypes_any"] = terms
                else:
                    return None
    return HandCardPredicateSpec(
        query=ObjectQuerySpec(**fields),
        excluded_supertypes=excluded_supertypes,
        mana_value_min=mana_value_min,
        mana_value_max=mana_value_max,
    )


def _tail_effect(
    value: str,
) -> tuple[Mapping[str, Any], tuple[str, ...]] | None:
    normalized = value.strip()
    if not normalized:
        return None
    scry = fixed_scry_effect_template(normalized)
    if scry is not None:
        _template, effects, target, mechanics = scry.compiled()
        if target is None and len(effects) == 1:
            return effects[0], mechanics
    life = fixed_life_effect_template(normalized)
    if life is not None:
        _template, effects, target, mechanics = life.compiled()
        if target is None and len(effects) == 1:
            return effects[0], mechanics
    return None


@dataclass(frozen=True, slots=True)
class FixedHandInspectionTemplate:
    relation: str
    mode: HandInspectionMode
    action: HandInspectionAction
    predicate: HandCardPredicateSpec | None = None
    trailing_effect: Mapping[str, Any] | None = None
    trailing_mechanics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.relation not in {"any", "opponent"}:
            raise ValueError("Hand inspection target relation is unsupported")
        if not isinstance(self.mode, HandInspectionMode) or not isinstance(
            self.action, HandInspectionAction
        ):
            raise ValueError("Hand inspection mode or action is malformed")
        selection_action = self.action in {
            HandInspectionAction.DISCARD,
            HandInspectionAction.EXILE,
            HandInspectionAction.DISCARD_ALL,
        }
        if selection_action != (self.predicate is not None):
            raise ValueError(
                "Hand inspection predicate does not match its action"
            )
        if self.trailing_effect is not None and self.action not in {
            HandInspectionAction.DISCARD,
            HandInspectionAction.EXILE,
        }:
            raise ValueError("Only one-card hand movement may have a fixed tail")
        object.__setattr__(
            self,
            "trailing_effect",
            deepcopy(self.trailing_effect),
        )

    @property
    def template_id(self) -> str:
        return "fixed-target-hand-inspection-program-v1"

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        effect = {
            "op": FIXED_HAND_INSPECTION_OPERATION,
            "player": "$controller",
            "target": "$target.0",
            "inspection": self.mode.value,
            "action": self.action.value,
            "predicate": (
                self.predicate.to_dict() if self.predicate is not None else None
            ),
        }
        effects = (effect,) + (
            (deepcopy(self.trailing_effect),)
            if self.trailing_effect is not None
            else ()
        )
        return (
            self.template_id,
            effects,
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": self.relation,
                "count": 1,
            },
            tuple(
                dict.fromkeys(
                    (
                        FIXED_HAND_INSPECTION_MECHANIC,
                        "cr-402-hand",
                        "cr-115-targets",
                        *self.trailing_mechanics,
                    )
                )
            ),
        )


def _choice_template(
    normalized: str,
) -> FixedHandInspectionTemplate | None:
    patterns = (
        (
            HandInspectionMode.REVEAL,
            HandInspectionAction.DISCARD,
            re.compile(
                r"^Target (?P<subject>opponent|player) reveals their hand\. "
                r"You choose (?P<quality>(?:a|an) (?:.+? )?card) from it"
                r"(?P<mana> with mana value \d+ or (?:less|greater))?\. "
                r"That player discards that card\.(?P<tail>.*)$",
                re.IGNORECASE,
            ),
        ),
        (
            HandInspectionMode.REVEAL,
            HandInspectionAction.EXILE,
            re.compile(
                r"^Target (?P<subject>opponent|player) reveals their hand\. "
                r"You choose (?P<quality>(?:a|an) (?:.+? )?card) from it"
                r"(?P<mana> with mana value \d+ or (?:less|greater))?"
                r"(?: and exile that card|\. "
                r"Exile that card)\.(?P<tail>.*)$",
                re.IGNORECASE,
            ),
        ),
        (
            HandInspectionMode.LOOK,
            HandInspectionAction.DISCARD,
            re.compile(
                r"^Look at target (?P<subject>opponent|player)'s hand and "
                r"choose (?P<quality>(?:a|an) (?:.+? )?card) from it"
                r"(?P<mana> with mana value \d+ or (?:less|greater))?\. "
                r"That player discards that card\.(?P<tail>.*)$",
                re.IGNORECASE,
            ),
        ),
    )
    for mode, action, pattern in patterns:
        match = pattern.fullmatch(normalized)
        if match is None:
            continue
        predicate = _quality_predicate(
            match.group("quality") + (match.group("mana") or "")
        )
        if predicate is None:
            return None
        raw_tail = match.group("tail").strip()
        compiled_tail = _tail_effect(raw_tail)
        if raw_tail and compiled_tail is None:
            return None
        return FixedHandInspectionTemplate(
            relation=(
                "opponent"
                if match.group("subject").casefold() == "opponent"
                else "any"
            ),
            mode=mode,
            action=action,
            predicate=predicate,
            trailing_effect=(compiled_tail[0] if compiled_tail else None),
            trailing_mechanics=(compiled_tail[1] if compiled_tail else ()),
        )
    return None


def fixed_hand_inspection_effect_template(
    text: str,
) -> FixedHandInspectionTemplate | None:
    """Lower one closed target-hand observation or immediate movement."""

    normalized = " ".join(text.strip().replace("’", "'").split())
    if normalized and not normalized.endswith("."):
        normalized += "."
    look = re.fullmatch(
        r"Look at target (?P<subject>opponent|player)'s hand\.",
        normalized,
        re.IGNORECASE,
    )
    if look is not None:
        return FixedHandInspectionTemplate(
            relation=(
                "opponent"
                if look.group("subject").casefold() == "opponent"
                else "any"
            ),
            mode=HandInspectionMode.LOOK,
            action=HandInspectionAction.OBSERVE,
        )
    bulk = re.fullmatch(
        r"Target (?P<subject>opponent|player) reveals their hand and "
        r"discards all (?P<quality>nonland|Trap) cards\.",
        normalized,
        re.IGNORECASE,
    )
    if bulk is not None:
        predicate = _quality_predicate(f"a {bulk.group('quality')} card")
        if predicate is None:
            return None
        return FixedHandInspectionTemplate(
            relation=(
                "opponent"
                if bulk.group("subject").casefold() == "opponent"
                else "any"
            ),
            mode=HandInspectionMode.REVEAL,
            action=HandInspectionAction.DISCARD_ALL,
            predicate=predicate,
        )
    return _choice_template(normalized)


__all__ = [
    "FIXED_HAND_INSPECTION_CAPABILITY",
    "FIXED_HAND_INSPECTION_MECHANIC",
    "FIXED_HAND_INSPECTION_OPERATION",
    "FixedHandInspectionTemplate",
    "HandInspectionAction",
    "HandInspectionMode",
    "fixed_hand_inspection_effect_template",
]
