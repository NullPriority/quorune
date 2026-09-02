from __future__ import annotations

"""Closed source-entry requirements returning controlled permanents to hand."""

from dataclasses import dataclass
from copy import deepcopy
import re
from typing import Any

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec


_COLOR_CODES = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_LAND_SUBTYPES = {"plains", "island", "swamp", "mountain", "forest"}
_COUNT_WORDS = {"a": 1, "an": 1, "another": 1, "two": 2, "three": 3}
FIXED_ENTRY_RETURN_CAPABILITY = "choice.controller.fixed_return_owner_hand"
FIXED_ENTRY_RETURN_MECHANIC = "fixed-entry-return-requirement"
_ENTRY_RETURN = re.compile(
    r"^When this (?P<source>creature|artifact|enchantment|land) enters, "
    r"(?:(?P<unless>sacrifice it unless you )?return )"
    r"(?P<count>a|an|another|two|three) (?P<quality>.+?) you control "
    r"to (?:its|their) owner's hand\.?$",
    re.IGNORECASE,
)
_EXTERNAL_ENTRY_SELF_RETURN = re.compile(
    r"^When a (?P<quality>[A-Za-z][A-Za-z '-]*) you control enters, "
    r"return this (?P<source>creature|artifact|enchantment|land) to its "
    r"owner's hand\.?$",
    re.IGNORECASE,
)
_OTHER_ENTRY_SELF_RETURN = re.compile(
    r"^When another (?P<quality>creature|artifact|enchantment|land) enters, "
    r"return this (?P<source>creature|artifact|enchantment|land) to its "
    r"owner's hand\.?$",
    re.IGNORECASE,
)
_RETURN_BODY = re.compile(
    r"^(?:(?P<unless>sacrifice it unless you )?return )"
    r"(?P<count>a|an|another|two|three) (?P<quality>.+?) you control "
    r"to (?:its|their) owner's hand\.?$",
    re.IGNORECASE,
)
_SELF_RETURN_BODY = re.compile(
    r"^return this (?:creature|artifact|enchantment|land) to its owner's hand\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedEntryReturnRequirementSpec:
    count: int
    predicate: ObjectQuerySpec
    excludes_source: bool
    sacrifice_source_unless_paid: bool
    event_predicate: ObjectQuerySpec | None = None
    return_source: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported entry-return requirement version")
        if type(self.count) is not int or self.count not in {1, 2, 3}:
            raise ValueError("Entry-return requirement count is unsupported")
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise ValueError("Entry-return requirements need a typed predicate")
        if type(self.excludes_source) is not bool or type(
            self.sacrifice_source_unless_paid
        ) is not bool or type(self.return_source) is not bool:
            raise ValueError("Entry-return requirement flags must be booleans")
        if self.event_predicate is not None and not isinstance(
            self.event_predicate, ObjectQuerySpec
        ):
            raise ValueError("Entry-return event predicates must be typed")
        if self.return_source and (
            self.count != 1
            or self.excludes_source
            or self.sacrifice_source_unless_paid
        ):
            raise ValueError("External entry self-return shape is malformed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "count": self.count,
            "predicate": self.predicate.to_dict(),
            "excludes_source": self.excludes_source,
            "sacrifice_source_unless_paid": self.sacrifice_source_unless_paid,
            "event_predicate": (
                self.event_predicate.to_dict()
                if self.event_predicate is not None
                else None
            ),
            "return_source": self.return_source,
        }

    @property
    def template_id(self) -> str:
        shape = "source" if self.return_source else f"choice-{self.count}"
        if self.sacrifice_source_unless_paid:
            shape += "-unless-sacrifice"
        return f"fixed-entry-return-{shape}-v1"

    def _choice_effect(self, *, full_payment: bool) -> dict[str, Any]:
        predicate = self.predicate.to_dict()
        predicate["controller"] = None
        predicate["known_to_actor"] = None
        predicate["exclude_ref"] = None
        effect: dict[str, Any] = {
            "op": "choose_cards_apnap",
            "actor": "$controller",
            "players": ["$controller"],
            "zone": "battlefield",
            "predicate": predicate,
            "count": self.count,
            "then": "return_owner_hand",
            "prompt": "Choose the required permanent(s) to return.",
        }
        if self.excludes_source:
            effect["exclude_ref"] = "$source"
        if full_payment:
            effect["require_full_count"] = True
            effect["fallback_effects"] = [
                {"op": "sacrifice_if_present", "card": "$source"}
            ]
        return effect

    @property
    def effects(self) -> tuple[dict[str, Any], ...]:
        if self.return_source:
            return ({"op": "bounce", "card": "$source"},)
        choice = self._choice_effect(
            full_payment=self.sacrifice_source_unless_paid
        )
        if not self.sacrifice_source_unless_paid:
            return (choice,)
        return (
            {
                "op": "choose_option",
                "player": "$controller",
                "prompt": "Return the required permanent(s) or sacrifice this permanent?",
                "options": [
                    {"id": "return", "label": "Return permanent(s)"},
                    {"id": "sacrifice", "label": "Sacrifice this permanent"},
                ],
                "then_by_choice": {
                    "return": [choice],
                    "sacrifice": [
                        {"op": "sacrifice_if_present", "card": "$source"}
                    ],
                },
            },
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            FIXED_ENTRY_RETURN_MECHANIC,
            "return-to-owner-hand",
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[dict[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return self.template_id, deepcopy(self.effects), None, self.mechanics


def _controlled_quality_query(
    quality: str,
    *,
    excludes_source: bool,
) -> ObjectQuerySpec | None:
    normalized = " ".join(quality.casefold().split())
    fields: dict[str, Any] = {
        "zones": ("battlefield",),
        "controller": "$actor",
        "known_to_actor": True,
        "exclude_ref": "$source" if excludes_source else None,
    }
    if normalized in {"permanent", "permanents"}:
        pass
    elif normalized in {"nonland permanent", "nonland permanents"}:
        fields["excluded_types"] = ("land",)
    elif normalized in {"land", "lands"}:
        fields["types_all"] = ("land",)
    elif normalized in {"non-lair land", "non-lair lands"}:
        fields["types_all"] = ("land",)
        fields["excluded_subtypes"] = ("lair",)
    elif normalized in {"creature", "creatures"}:
        fields["types_all"] = ("creature",)
    elif normalized in {"artifact", "artifacts"}:
        fields["types_all"] = ("artifact",)
    elif normalized.startswith("untapped "):
        subtype = normalized.removeprefix("untapped ").removesuffix("s")
        if subtype not in _LAND_SUBTYPES:
            return None
        fields["types_all"] = ("land",)
        fields["subtypes_all"] = (subtype,)
        fields["tapped"] = False
    else:
        color_match = re.fullmatch(
            r"(?P<colors>white|blue|black|red|green"
            r"(?: or (?:white|blue|black|red|green))?) creatures?",
            normalized,
        )
        if color_match is None:
            return None
        fields["types_all"] = ("creature",)
        fields["colors_any"] = tuple(
            _COLOR_CODES[value]
            for value in color_match.group("colors").split(" or ")
        )
    return ObjectQuerySpec(**fields)


def fixed_entry_return_requirement_spec(
    text: str,
) -> FixedEntryReturnRequirementSpec | None:
    """Parse one fixed source-entry return or sacrifice-unless-return body."""

    normalized = " ".join(text.strip().split())
    other = _OTHER_ENTRY_SELF_RETURN.fullmatch(normalized)
    if other is not None:
        subject_type = other.group("quality").casefold()
        return FixedEntryReturnRequirementSpec(
            count=1,
            predicate=ObjectQuerySpec(
                zones=("battlefield",),
                exclude_ref="$source",
                known_to_actor=True,
            ),
            excludes_source=False,
            sacrifice_source_unless_paid=False,
            event_predicate=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=(subject_type,),
                exclude_ref="$source",
                known_to_actor=True,
            ),
            return_source=True,
        )
    external = _EXTERNAL_ENTRY_SELF_RETURN.fullmatch(normalized)
    if external is not None:
        quality = external.group("quality").casefold()
        subtype = canonical_creature_subtype(quality) or (
            quality if quality == "cartouche" else None
        )
        if subtype is None:
            return None
        return FixedEntryReturnRequirementSpec(
            count=1,
            predicate=ObjectQuerySpec(
                zones=("battlefield",),
                controller="$actor",
                exclude_ref="$source",
                known_to_actor=True,
            ),
            excludes_source=False,
            sacrifice_source_unless_paid=False,
            event_predicate=ObjectQuerySpec(
                zones=("battlefield",),
                controller="$actor",
                subtypes_all=(subtype,),
                exclude_ref="$source",
                known_to_actor=True,
            ),
            return_source=True,
        )
    match = _ENTRY_RETURN.fullmatch(normalized)
    if match is None:
        return None
    count_word = match.group("count").casefold()
    excludes_source = count_word == "another"
    predicate = _controlled_quality_query(
        match.group("quality"),
        excludes_source=excludes_source,
    )
    if predicate is None:
        return None
    return FixedEntryReturnRequirementSpec(
        count=_COUNT_WORDS[count_word],
        predicate=predicate,
        excludes_source=excludes_source,
        sacrifice_source_unless_paid=match.group("unless") is not None,
    )


def fixed_entry_return_effect_template(
    text: str,
) -> tuple[
    str | None,
    tuple[dict[str, Any], ...],
    None,
    tuple[str, ...],
]:
    """Lower the body of one already-bound fixed entry-return trigger."""

    normalized = " ".join(text.strip().split())
    if _SELF_RETURN_BODY.fullmatch(normalized) is not None:
        return FixedEntryReturnRequirementSpec(
            count=1,
            predicate=ObjectQuerySpec(zones=("battlefield",)),
            excludes_source=False,
            sacrifice_source_unless_paid=False,
            return_source=True,
        ).compiled()
    match = _RETURN_BODY.fullmatch(normalized)
    if match is None:
        return None, (), None, ()
    count_word = match.group("count").casefold()
    excludes_source = count_word == "another"
    predicate = _controlled_quality_query(
        match.group("quality"),
        excludes_source=excludes_source,
    )
    if predicate is None:
        return None, (), None, ()
    return FixedEntryReturnRequirementSpec(
        count=_COUNT_WORDS[count_word],
        predicate=predicate,
        excludes_source=excludes_source,
        sacrifice_source_unless_paid=match.group("unless") is not None,
    ).compiled()


__all__ = [
    "FIXED_ENTRY_RETURN_CAPABILITY",
    "FIXED_ENTRY_RETURN_MECHANIC",
    "FixedEntryReturnRequirementSpec",
    "fixed_entry_return_effect_template",
    "fixed_entry_return_requirement_spec",
]
