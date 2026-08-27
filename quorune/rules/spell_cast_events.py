from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Mapping

from ..util import stable_json


class SpellCastEventError(ValueError):
    """A normalized spell-cast event is malformed or unsupported."""


def _identity(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpellCastEventError(f"{field} must be a nonempty string")
    return value.strip()


def _terms(
    values: Iterable[str],
    *,
    field: str,
    required: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise SpellCastEventError(
            f"Spell {field} must be an iterable of strings"
        )
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise SpellCastEventError(
                f"Spell {field} must contain nonempty strings"
            )
        normalized.add(value.strip().casefold())
    if required and not normalized:
        raise SpellCastEventError("A cast spell must have at least one card type")
    return tuple(sorted(normalized))


def _colors(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise SpellCastEventError("Spell colors must be an iterable of strings")
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value.strip().upper() not in "WUBRG":
            raise SpellCastEventError(
                "Spell colors must contain only W, U, B, R, or G"
            )
        normalized.add(value.strip().upper())
    return tuple(color for color in "WUBRG" if color in normalized)


@dataclass(frozen=True, slots=True)
class SpellCastEvent:
    """Immutable facts captured when CR 601.2i makes a spell cast."""

    card_ref: str
    object_id: str
    logical_object_id: str
    controller: str
    origin: str
    stack_ref: str
    types: tuple[str, ...]
    subtypes: tuple[str, ...] = ()
    supertypes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    schema_version: int = 2

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise SpellCastEventError(
                "Unsupported normalized spell-cast event schema version"
            )
        for field in (
            "card_ref",
            "object_id",
            "logical_object_id",
            "controller",
            "origin",
            "stack_ref",
        ):
            object.__setattr__(
                self,
                field,
                _identity(getattr(self, field), field=field),
            )
        object.__setattr__(
            self,
            "types",
            _terms(self.types, field="card types", required=True),
        )
        object.__setattr__(
            self,
            "subtypes",
            _terms(self.subtypes, field="subtypes"),
        )
        object.__setattr__(
            self,
            "supertypes",
            _terms(self.supertypes, field="supertypes"),
        )
        object.__setattr__(self, "colors", _colors(self.colors))
        if self.schema_version == 1 and (
            self.subtypes or self.supertypes or self.colors
        ):
            raise SpellCastEventError(
                "Legacy spell-cast events cannot carry v2 characteristics"
            )

    def to_context(self) -> dict[str, Any]:
        context = {
            "schema_version": self.schema_version,
            "card": self.card_ref,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "controller": self.controller,
            "player": self.controller,
            "from": self.origin,
            "to": "stack",
            "types": list(self.types),
            "stack": self.stack_ref,
        }
        if self.schema_version == 2:
            context.update(
                {
                    "subtypes": list(self.subtypes),
                    "supertypes": list(self.supertypes),
                    "colors": list(self.colors),
                }
            )
        return context

    @classmethod
    def from_context(cls, value: Mapping[str, Any]) -> "SpellCastEvent":
        if not isinstance(value, Mapping):
            raise SpellCastEventError(
                "Normalized spell-cast events have a closed schema"
            )
        version = value.get("schema_version")
        expected = {
            "schema_version",
            "card",
            "object_id",
            "logical_object_id",
            "controller",
            "player",
            "from",
            "to",
            "types",
            "stack",
        }
        if version == 2:
            expected.update({"subtypes", "supertypes", "colors"})
        elif version != 1:
            raise SpellCastEventError(
                "Unsupported normalized spell-cast event schema version"
            )
        if set(value) != expected:
            raise SpellCastEventError(
                "Normalized spell-cast events have a closed schema"
            )
        if value["player"] != value["controller"]:
            raise SpellCastEventError(
                "Spell-cast player and controller must agree"
            )
        if value["to"] != "stack":
            raise SpellCastEventError(
                "Normalized spell-cast events must describe the stack move"
            )
        arrays = {
            "types": value["types"],
            "subtypes": value.get("subtypes", ()),
            "supertypes": value.get("supertypes", ()),
            "colors": value.get("colors", ()),
        }
        if any(not isinstance(item, (list, tuple)) for item in arrays.values()):
            raise SpellCastEventError(
                "Spell-cast characteristics must be arrays"
            )
        return cls(
            schema_version=version,
            card_ref=value["card"],
            object_id=value["object_id"],
            logical_object_id=value["logical_object_id"],
            controller=value["controller"],
            origin=value["from"],
            stack_ref=value["stack"],
            types=tuple(arrays["types"]),
            subtypes=tuple(arrays["subtypes"]),
            supertypes=tuple(arrays["supertypes"]),
            colors=tuple(arrays["colors"]),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_context()).encode("utf-8")
        ).hexdigest()


__all__ = ["SpellCastEvent", "SpellCastEventError"]
