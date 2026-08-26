from __future__ import annotations

"""Closed effects that restrict one creature's declarations this turn."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..rules.temporary_declaration_restrictions import (
    TemporaryDeclarationRestrictionKind,
    temporary_declaration_restriction,
)
from .creature_subtypes import canonical_creature_subtype


_TARGET_TEMPORARY_DECLARATION_RESTRICTION = re.compile(
    r"target creature can't (?P<restriction>attack or block|attack|block|be blocked) "
    r"this turn\.?",
    re.IGNORECASE,
)
_TARGET_SUBTYPE_UNBLOCKABLE = re.compile(
    r"target (?P<subtype>[A-Za-z][A-Za-z'\N{RIGHT SINGLE QUOTATION MARK}-]*) "
    r"can't be blocked this turn\.?",
    re.IGNORECASE,
)
_SOURCE_TEMPORARY_DECLARATION_RESTRICTION = re.compile(
    r"(?P<source>.+?) can't "
    r"(?P<restriction>attack or block|attack|block|be blocked) this turn\.?",
    re.IGNORECASE,
)
_RESTRICTION_KINDS: dict[str, TemporaryDeclarationRestrictionKind] = {
    "attack": "cant_attack",
    "block": "cant_block",
    "attack or block": "cant_attack_or_block",
    "be blocked": "unblockable",
}


@dataclass(frozen=True, slots=True)
class ActivatedTemporaryDeclarationRestrictionTemplate:
    restriction: TemporaryDeclarationRestrictionKind
    card_reference: str = "$target.0"
    creature_subtype: str | None = None

    def __post_init__(self) -> None:
        temporary_declaration_restriction(self.restriction)
        if self.card_reference not in {"$source", "$target.0"}:
            raise ValueError("Temporary declaration subject is unsupported")
        if self.creature_subtype is not None and (
            self.card_reference != "$target.0"
            or canonical_creature_subtype(self.creature_subtype)
            != self.creature_subtype
        ):
            raise ValueError(
                "Temporary declaration creature subtype is unsupported"
            )

    @property
    def template_id(self) -> str:
        subject = (
            "temporary-source"
            if self.card_reference == "$source"
            else f"temporary-target-{self.creature_subtype}"
            if self.creature_subtype is not None
            else "activated-target"
        )
        return f"{subject}-{self.restriction.replace('_', '-')}-eot-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "grant_declaration_restriction_until_end_of_turn",
                "card": self.card_reference,
                "restriction": self.restriction,
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.card_reference == "$source":
            return None
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "count": 1,
        }
        if self.creature_subtype is None:
            schema["types_any"] = ["creature"]
        else:
            schema["subtypes_any"] = [self.creature_subtype]
        return schema

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        restriction = temporary_declaration_restriction(self.restriction)
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            (
                *(("cr-115-targets",) if self.target_schema else ()),
                "cr-611-continuous-effects",
                *restriction.mechanics,
            ),
        )


def activated_temporary_declaration_restriction_effect_template(
    text: str, *, card_name: str = ""
) -> ActivatedTemporaryDeclarationRestrictionTemplate | None:
    """Parse one closed activated temporary declaration instruction."""

    return temporary_declaration_restriction_effect_template(
        text,
        card_name=card_name,
        allow_source=True,
    )


def temporary_declaration_restriction_effect_template(
    text: str,
    *,
    card_name: str,
    allow_source: bool,
) -> ActivatedTemporaryDeclarationRestrictionTemplate | None:
    """Parse only fixed target or source creature restrictions this turn."""

    normalized = " ".join(text.strip().split())
    match = _TARGET_TEMPORARY_DECLARATION_RESTRICTION.fullmatch(normalized)
    if match is not None:
        return ActivatedTemporaryDeclarationRestrictionTemplate(
            _RESTRICTION_KINDS[match.group("restriction").casefold()]
        )
    subtype_match = _TARGET_SUBTYPE_UNBLOCKABLE.fullmatch(normalized)
    if subtype_match is not None:
        subtype = canonical_creature_subtype(subtype_match.group("subtype"))
        if subtype is not None and subtype != "creature":
            return ActivatedTemporaryDeclarationRestrictionTemplate(
                "unblockable",
                creature_subtype=subtype,
            )
    if not allow_source:
        return None
    source_match = _SOURCE_TEMPORARY_DECLARATION_RESTRICTION.fullmatch(
        normalized
    )
    if source_match is None:
        return None
    source = source_match.group("source").casefold()
    source_names = {"this creature"}
    source_names.update(
        part.strip().casefold() for part in card_name.split("//")
    )
    source_names.discard("")
    if source not in source_names:
        return None
    return ActivatedTemporaryDeclarationRestrictionTemplate(
        _RESTRICTION_KINDS[source_match.group("restriction").casefold()],
        card_reference="$source",
    )


__all__ = [
    "ActivatedTemporaryDeclarationRestrictionTemplate",
    "activated_temporary_declaration_restriction_effect_template",
    "temporary_declaration_restriction_effect_template",
]
