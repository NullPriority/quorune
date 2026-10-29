from __future__ import annotations

"""Closed CR 201.5/201.5c source-name references.

This model does not guess arbitrary nicknames.  It recognizes the full Oracle
name and one complete leading name separated by a comma, a represented title
delimiter, or an ordinary two-word name.  Those are the bounded shortened
forms used by the represented Oracle grammar.  Other abbreviations remain
residuals.
"""

from dataclasses import dataclass
import re

from ..util import normalize_card_name


SOURCE_REFERENCE_SCHEMA_VERSION = 2


_SOURCE_SELF_PERMANENT_TYPES = {
    "this artifact": "artifact",
    "this aura": "enchantment",
    "this battle": "battle",
    "this creature": "creature",
    "this enchantment": "enchantment",
    "this equipment": "artifact",
    "this land": "land",
    "this permanent": "permanent",
    "this planeswalker": "planeswalker",
    "this saga": "enchantment",
    "this spacecraft": "artifact",
    "this vehicle": "artifact",
}


class SourceReferenceError(ValueError):
    """Raised when a source-reference vocabulary cannot be constructed."""


def source_self_permanent_type(candidate: object) -> str | None:
    """Return a compile-time self descriptor's canonical permanent type.

    Oracle's ``this Aura``-style wording identifies the source object; the
    descriptor is not a resolution-time characteristic predicate.  Returning
    the canonical card type lets compiler templates retain stable labels while
    their executable reference remains the physical ``$source`` identity.
    """

    if type(candidate) is not str:
        return None
    normalized = " ".join(candidate.casefold().split())
    return _SOURCE_SELF_PERMANENT_TYPES.get(normalized)


def source_self_permanent_pattern() -> str:
    """Return the compiler regex for the same closed self-label vocabulary."""
    return '(?:' + '|'.join(re.escape(value) for value in _SOURCE_SELF_PERMANENT_TYPES) + ')'


@dataclass(frozen=True, slots=True, init=False)
class SourceReferenceSpec:
    """Immutable full and bounded-shortened names for one source object."""

    schema_version: int
    full_name: str
    shortened_name: str | None
    normalized_names: tuple[str, ...]

    def __init__(self, card_name: str):
        if type(card_name) is not str:
            raise SourceReferenceError("Source name must be a string")
        full_name = " ".join(card_name.split())
        if not full_name:
            raise SourceReferenceError("Source name must be nonempty")
        normalized_full = normalize_card_name(full_name)
        if not normalized_full:
            raise SourceReferenceError("Source name must be nonempty")

        shortened_name: str | None = None
        normalized_names = [normalized_full]
        if "," in full_name:
            leading, suffix = full_name.split(",", 1)
            leading = leading.strip()
            if not leading or not suffix.strip():
                raise SourceReferenceError("Malformed comma-qualified source name")
            normalized_leading = normalize_card_name(leading)
            if normalized_leading and normalized_leading != normalized_full:
                shortened_name = leading
                normalized_names.append(normalized_leading)
        else:
            title = re.fullmatch(
                r"(?P<leading>.+?)\s+(?:the|of)\s+"
                r"(?P<title>\S(?:.*\S)?)",
                full_name,
                re.IGNORECASE,
            )
            two_word = re.fullmatch(
                r"(?P<leading>\S+)\s+(?P<title>\S+)",
                full_name,
            )
            bounded = title or two_word
            if bounded is not None:
                leading = bounded.group("leading").strip()
                normalized_leading = normalize_card_name(leading)
                if normalized_leading and normalized_leading != normalized_full:
                    shortened_name = leading
                    normalized_names.append(normalized_leading)

        object.__setattr__(self, "schema_version", SOURCE_REFERENCE_SCHEMA_VERSION)
        object.__setattr__(self, "full_name", full_name)
        object.__setattr__(self, "shortened_name", shortened_name)
        object.__setattr__(self, "normalized_names", tuple(normalized_names))

    @property
    def display_names(self) -> tuple[str, ...]:
        return (
            (self.full_name, self.shortened_name)
            if self.shortened_name is not None
            else (self.full_name,)
        )

    @property
    def regex_pattern(self) -> str:
        values = sorted(self.display_names, key=len, reverse=True)
        return "(?:" + "|".join(re.escape(value) for value in values) + ")"

    def matches(self, candidate: object) -> bool:
        if type(candidate) is not str:
            return False
        normalized = normalize_card_name(candidate)
        return bool(normalized) and normalized in self.normalized_names


__all__ = [
    "SOURCE_REFERENCE_SCHEMA_VERSION",
    "SourceReferenceError",
    "SourceReferenceSpec",
    "source_self_permanent_type",
    "source_self_permanent_pattern",
]
