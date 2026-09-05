from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any, Iterable, Mapping

from ..util import stable_json


class SpellCastEventError(ValueError):
    """A normalized spell-cast event is malformed or unsupported."""


_TURN_PHASES = frozenset(
    {"beginning", "precombat_main", "combat", "postcombat_main", "ending"}
)


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


def _references(values: Iterable[str], *, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise SpellCastEventError(f"Spell {field} must be an iterable of strings")
    return tuple(_identity(value, field=field) for value in values)


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
    mana_value: float | None = None
    owner: str | None = None
    active_player: str | None = None
    caster_spell_number: int | None = None
    kicked: bool | None = None
    has_x_cost: bool | None = None
    has_adventure: bool | None = None
    keywords: tuple[str, ...] | None = None
    phase: str | None = None
    targets: tuple[str, ...] | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version not in {1, 2, 3, 4, 5}
        ):
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
        extended = (
            self.mana_value,
            self.owner,
            self.active_player,
            self.caster_spell_number,
            self.kicked,
            self.has_x_cost,
            self.has_adventure,
            self.keywords,
            self.phase,
            self.targets,
        )
        if self.schema_version in {1, 2}:
            if any(value is not None for value in extended):
                raise SpellCastEventError(
                    "Legacy spell-cast events cannot carry v3 cast facts"
                )
            return
        if (
            type(self.mana_value) not in {int, float}
            or not math.isfinite(float(self.mana_value))
            or float(self.mana_value) < 0
        ):
            raise SpellCastEventError(
                "Spell mana value must be a finite nonnegative number"
            )
        object.__setattr__(self, "mana_value", float(self.mana_value))
        object.__setattr__(self, "owner", _identity(self.owner, field="owner"))
        object.__setattr__(
            self,
            "active_player",
            _identity(self.active_player, field="active_player"),
        )
        if (
            type(self.caster_spell_number) is not int
            or self.caster_spell_number <= 0
        ):
            raise SpellCastEventError(
                "Caster spell number must be a positive integer"
            )
        if any(
            type(value) is not bool
            for value in (self.kicked, self.has_x_cost, self.has_adventure)
        ):
            raise SpellCastEventError(
                "Kicked, X-cost, and Adventure cast facts must be booleans"
            )
        if self.keywords is None:
            raise SpellCastEventError(
                "Spell keywords must be present in v3 cast facts"
            )
        object.__setattr__(
            self,
            "keywords",
            _terms(self.keywords, field="keywords"),
        )
        if self.schema_version in {1, 2, 3}:
            if self.phase is not None:
                raise SpellCastEventError(
                    "Legacy cast events cannot carry a phase"
                )
        else:
            phase = _identity(self.phase, field="phase")
            if phase not in _TURN_PHASES:
                raise SpellCastEventError(
                    "Normalized spell-cast phase is unsupported"
                )
            object.__setattr__(self, "phase", phase)
        if self.schema_version in {1, 2, 3, 4}:
            if self.targets is not None:
                raise SpellCastEventError(
                    "Legacy cast events cannot carry selected targets"
                )
        else:
            if self.targets is None:
                raise SpellCastEventError(
                    "Spell targets must be present in v5 cast facts"
                )
            object.__setattr__(
                self,
                "targets",
                _references(self.targets, field="targets"),
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
        if self.schema_version in {2, 3, 4, 5}:
            context.update(
                {
                    "subtypes": list(self.subtypes),
                    "supertypes": list(self.supertypes),
                    "colors": list(self.colors),
                }
            )
        if self.schema_version in {3, 4, 5}:
            context.update(
                {
                    "mana_value": self.mana_value,
                    "owner": self.owner,
                    "active_player": self.active_player,
                    "caster_spell_number": self.caster_spell_number,
                    "kicked": self.kicked,
                    "has_x_cost": self.has_x_cost,
                    "has_adventure": self.has_adventure,
                    "keywords": list(self.keywords or ()),
                    **(
                        {"phase": self.phase}
                        if self.schema_version in {4, 5}
                        else {}
                    ),
                    **(
                        {"targets": list(self.targets or ())}
                        if self.schema_version == 5
                        else {}
                    ),
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
        if version in {2, 3, 4, 5}:
            expected.update({"subtypes", "supertypes", "colors"})
        if version in {3, 4, 5}:
            expected.update(
                {
                    "mana_value",
                    "owner",
                    "active_player",
                    "caster_spell_number",
                    "kicked",
                    "has_x_cost",
                    "has_adventure",
                    "keywords",
                }
            )
            if version in {4, 5}:
                expected.add("phase")
            if version == 5:
                expected.add("targets")
        elif version not in {1, 2}:
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
            "keywords": value.get("keywords", ()),
            "targets": value.get("targets", ()),
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
            mana_value=value.get("mana_value"),
            owner=value.get("owner"),
            active_player=value.get("active_player"),
            caster_spell_number=value.get("caster_spell_number"),
            kicked=value.get("kicked"),
            has_x_cost=value.get("has_x_cost"),
            has_adventure=value.get("has_adventure"),
            keywords=(
                tuple(arrays["keywords"])
                if version in {3, 4, 5}
                else None
            ),
            phase=value.get("phase"),
            targets=(
                tuple(arrays["targets"])
                if version == 5
                else None
            ),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_context()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class SpellCopyEvent:
    """Immutable public facts captured when a spell copy is created."""

    card_ref: str
    object_id: str
    logical_object_id: str
    controller: str
    stack_ref: str
    copied_from_stack_ref: str
    types: tuple[str, ...]
    targets: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise SpellCastEventError(
                "Unsupported normalized spell-copy event schema version"
            )
        for field in (
            "card_ref",
            "object_id",
            "logical_object_id",
            "controller",
            "stack_ref",
            "copied_from_stack_ref",
        ):
            object.__setattr__(
                self,
                field,
                _identity(getattr(self, field), field=field),
            )
        object.__setattr__(
            self,
            "types",
            _terms(self.types, field="copy card types", required=True),
        )
        object.__setattr__(
            self,
            "targets",
            _references(self.targets, field="copy targets"),
        )

    def to_context(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "card": self.card_ref,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "controller": self.controller,
            "player": self.controller,
            "from": "stack",
            "to": "stack",
            "types": list(self.types),
            "targets": list(self.targets),
            "stack": self.stack_ref,
            "copied_from_stack": self.copied_from_stack_ref,
        }

    @classmethod
    def from_context(cls, value: Mapping[str, Any]) -> "SpellCopyEvent":
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
            "targets",
            "stack",
            "copied_from_stack",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise SpellCastEventError(
                "Normalized spell-copy events have a closed schema"
            )
        if value["player"] != value["controller"]:
            raise SpellCastEventError(
                "Spell-copy player and controller must agree"
            )
        if value["from"] != "stack" or value["to"] != "stack":
            raise SpellCastEventError(
                "Normalized spell-copy events must describe stack creation"
            )
        if not isinstance(value["types"], (list, tuple)) or not isinstance(
            value["targets"], (list, tuple)
        ):
            raise SpellCastEventError(
                "Spell-copy types and targets must be arrays"
            )
        return cls(
            schema_version=value["schema_version"],
            card_ref=value["card"],
            object_id=value["object_id"],
            logical_object_id=value["logical_object_id"],
            controller=value["controller"],
            stack_ref=value["stack"],
            copied_from_stack_ref=value["copied_from_stack"],
            types=tuple(value["types"]),
            targets=tuple(value["targets"]),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_context()).encode("utf-8")
        ).hexdigest()


__all__ = ["SpellCastEvent", "SpellCastEventError", "SpellCopyEvent"]
