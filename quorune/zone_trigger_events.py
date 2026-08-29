from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Literal, Sequence

from .characteristic_evaluation import type_parts
from .errors import GameRuleError
from .replacement.immutable import (
    FrozenMap,
    ImmutableValueError,
    thaw_value,
)
from .util import stable_json


ZoneEventSourceTiming = Literal["before", "after"]
_SACRIFICE_TRANSITION_KIND = "sacrifice"


class ZoneTransitionKind(str, Enum):
    """Closed semantic cause needed by normalized zone-event derivation."""

    ORDINARY = "ordinary"
    COUNTERED_SPELL = "countered_spell"
    SACRIFICE = _SACRIFICE_TRANSITION_KIND


class ZoneTriggerEventError(ValueError):
    """A normalized zone-change trigger occurrence is malformed."""


_EXILE_ZONE = "exile"
_LIBRARY_ZONE = "library"
_ZONE_CHANGE_DESTINATIONS = frozenset(
    {_LIBRARY_ZONE, "hand", "battlefield", "graveyard", _EXILE_ZONE, "command", "outside"}
)


def validate_zone_transition_request(
    cards: Mapping[str, Any],
    object_id: str,
    destination: Any,
    transition_kind: Any,
) -> Any:
    """Fail before mutation when a committed move has an invalid event cause."""

    if destination not in _ZONE_CHANGE_DESTINATIONS:
        raise GameRuleError(f"Unsupported destination {destination}")
    if not isinstance(transition_kind, ZoneTransitionKind):
        raise GameRuleError("Zone transitions require a supported typed event kind")
    card = cards[object_id]
    if transition_kind is ZoneTransitionKind.COUNTERED_SPELL and card.zone != "stack":
        raise GameRuleError(
            "Only a physical spell on the stack can use the countered-spell transition"
        )
    if transition_kind is ZoneTransitionKind.SACRIFICE and card.zone != "battlefield":
        raise GameRuleError(
            "Only a battlefield permanent can use the sacrifice transition"
        )
    return card


def normalized_transition_kind_map(
    changes: Sequence[tuple[str, str]],
    values: Mapping[str, ZoneTransitionKind] | None,
) -> dict[str, ZoneTransitionKind]:
    """Validate closed per-object transition causes for one move batch."""

    result = dict(values or {})
    changed_ids = {object_id for object_id, _destination in changes}
    if not set(result).issubset(changed_ids) or any(
        not isinstance(value, ZoneTransitionKind)
        for value in result.values()
    ):
        raise GameRuleError(
            "Simultaneous transition kinds must be typed and name changed objects"
        )
    return result


def normalized_library_position(
    destination: str,
    position: str | int,
) -> str | int | None:
    """Normalize a library insertion request, or ignore it for another zone."""

    if destination != _LIBRARY_ZONE:
        return None
    if isinstance(position, bool):
        raise GameRuleError(
            "Library position must be top, bottom, or a positive N"
        )
    if isinstance(position, int):
        if position < 1:
            raise GameRuleError(
                "Nth-from-top library position must be positive"
            )
        return position
    normalized = str(position).strip().casefold()
    if normalized not in {"top", "bottom"}:
        raise GameRuleError(
            "Library position must be top, bottom, or a positive N"
        )
    return normalized


def _string(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise ZoneTriggerEventError(f"{field} must be a nonempty string")
    return value


def _optional_string(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field=field)


def _string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ZoneTriggerEventError(f"{field} must be an array")
    result = tuple(
        _string(item, field=f"{field}[{index}]")
        for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise ZoneTriggerEventError(f"{field} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class ZoneChangeOccurrence:
    """Immutable CR 603.6 facts captured around one committed zone change."""

    object_id: str
    card_ref: str
    owner: str
    origin: str
    destination: str
    previous_controller: str
    current_controller: str
    previous_logical_object_id: str
    current_logical_object_id: str
    zone_change_counter: int
    token: bool
    card_object: bool
    previous_characteristics: FrozenMap
    current_characteristics: FrozenMap
    previous_attachments: tuple[str, ...] = ()
    previous_attached_to: str | None = None
    tapped: bool = False
    cause: str = ""
    transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY
    read_ahead_chapter: int | None = None
    cast_option: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        for field in (
            "object_id",
            "card_ref",
            "owner",
            "origin",
            "destination",
            "previous_controller",
            "current_controller",
            "previous_logical_object_id",
            "current_logical_object_id",
        ):
            _string(getattr(self, field), field=f"zone_occurrence.{field}")
        if type(self.zone_change_counter) is not int or self.zone_change_counter < 0:
            raise ZoneTriggerEventError(
                "zone_occurrence.zone_change_counter must be a nonnegative integer"
            )
        for field in ("token", "card_object", "tapped"):
            if type(getattr(self, field)) is not bool:
                raise ZoneTriggerEventError(
                    f"zone_occurrence.{field} must be a boolean"
                )
        if type(self.cause) is not str:
            raise ZoneTriggerEventError("zone_occurrence.cause must be a string")
        if not isinstance(self.transition_kind, ZoneTransitionKind):
            raise ZoneTriggerEventError(
                "zone_occurrence.transition_kind must be a supported typed value"
            )
        if self.read_ahead_chapter is not None and (
            type(self.read_ahead_chapter) is not int
            or self.read_ahead_chapter < 1
        ):
            raise ZoneTriggerEventError(
                "zone_occurrence.read_ahead_chapter must be positive or null"
            )
        if self.cast_option is not None and (
            type(self.cast_option) is not str or not self.cast_option
        ):
            raise ZoneTriggerEventError(
                "zone_occurrence.cast_option must be nonempty or null"
            )
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ZoneTriggerEventError(
                "Unsupported zone-trigger occurrence schema version"
            )
        for field in ("previous_characteristics", "current_characteristics"):
            value = getattr(self, field)
            if not isinstance(value, Mapping):
                raise ZoneTriggerEventError(
                    f"zone_occurrence.{field} must be an object"
                )
            if not isinstance(value, FrozenMap):
                try:
                    value = FrozenMap(value)
                except ImmutableValueError as exc:
                    raise ZoneTriggerEventError(
                        f"zone_occurrence.{field} is not canonical"
                    ) from exc
                object.__setattr__(self, field, value)
        if not isinstance(self.previous_attachments, tuple):
            object.__setattr__(
                self,
                "previous_attachments",
                _string_tuple(
                    self.previous_attachments,
                    field="zone_occurrence.previous_attachments",
                ),
            )
        else:
            _string_tuple(
                self.previous_attachments,
                field="zone_occurrence.previous_attachments",
            )
        _optional_string(
            self.previous_attached_to,
            field="zone_occurrence.previous_attached_to",
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "object_id": self.object_id,
            "card_ref": self.card_ref,
            "owner": self.owner,
            "origin": self.origin,
            "destination": self.destination,
            "previous_controller": self.previous_controller,
            "current_controller": self.current_controller,
            "previous_logical_object_id": self.previous_logical_object_id,
            "current_logical_object_id": self.current_logical_object_id,
            "zone_change_counter": self.zone_change_counter,
            "token": self.token,
            "card_object": self.card_object,
            "previous_characteristics": thaw_value(
                self.previous_characteristics
            ),
            "current_characteristics": thaw_value(
                self.current_characteristics
            ),
            "previous_attachments": list(self.previous_attachments),
            "previous_attached_to": self.previous_attached_to,
            "tapped": self.tapped,
            "cause": self.cause,
        }
        # Preserve ordinary occurrence fingerprints while making the corrected
        # counter path explicit and replay-stable.
        if self.transition_kind is not ZoneTransitionKind.ORDINARY:
            result["transition_kind"] = self.transition_kind.value
        if self.read_ahead_chapter is not None:
            result["read_ahead_chapter"] = self.read_ahead_chapter
        if self.cast_option is not None:
            result["cast_option"] = self.cast_option
        return result

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class NormalizedZoneTriggerEvent:
    """One closed event kind derived from a committed zone occurrence."""

    kind: str
    source_timing: ZoneEventSourceTiming
    context: FrozenMap

    def __post_init__(self) -> None:
        _string(self.kind, field="zone_trigger_event.kind")
        if self.source_timing not in {"before", "after"}:
            raise ZoneTriggerEventError(
                "zone_trigger_event.source_timing must be before or after"
            )
        if not isinstance(self.context, FrozenMap):
            object.__setattr__(self, "context", FrozenMap(self.context))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_timing": self.source_timing,
            "context": thaw_value(self.context),
        }


def _type_parts(characteristics: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    types, subtypes, _supertypes = type_parts(
        str(characteristics.get("type_line") or "")
    )
    return types, subtypes


def _public_numeric_power(characteristics: Mapping[str, Any]) -> int | None:
    """Read a sealed integer power without re-entering characteristic evaluation."""

    value = characteristics.get("power")
    if type(value) is int:
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value.strip())
    return None


def _public_colors(characteristics: Mapping[str, Any]) -> list[str]:
    values = characteristics.get("colors") or ()
    if isinstance(values, (str, bytes)):
        return []
    return sorted(
        {
            str(value).strip().upper()
            for value in values
            if str(value).strip().upper() in {"W", "U", "B", "R", "G"}
        }
    )


def sealed_public_characteristic_facts(
    characteristics: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize public facts from one already-sealed characteristic view."""

    types, subtypes = _type_parts(characteristics)
    return {
        "types": sorted(types),
        "subtypes": sorted(subtypes),
        "colors": _public_colors(characteristics),
        "power": _public_numeric_power(characteristics),
    }


def normalized_zone_trigger_events(
    occurrence: ZoneChangeOccurrence,
) -> tuple[NormalizedZoneTriggerEvent, ...]:
    """Derive the represented CR 603.6 event vocabulary without state access."""

    previous_facts = sealed_public_characteristic_facts(
        occurrence.previous_characteristics
    )
    previous_types = set(previous_facts["types"])
    common = {
        "card": occurrence.card_ref,
        "card_object_identity": occurrence.previous_logical_object_id,
        "card_zone_change_counter": occurrence.zone_change_counter,
        "owner": occurrence.owner,
        "controller": occurrence.current_controller,
        "previous_controller": occurrence.previous_controller,
        "from": occurrence.origin,
        "to": occurrence.destination,
        "cause": occurrence.cause,
        "token": occurrence.token,
        "attachments": list(occurrence.previous_attachments),
        "attached_to": occurrence.previous_attached_to,
        **previous_facts,
    }
    if occurrence.cast_option is not None:
        common["cast_option"] = occurrence.cast_option
    result: list[NormalizedZoneTriggerEvent] = []
    if occurrence.transition_kind is ZoneTransitionKind.SACRIFICE:
        result.append(
            NormalizedZoneTriggerEvent(
                "permanent.sacrificed", "before", FrozenMap(common)
            )
        )
    if occurrence.transition_kind is ZoneTransitionKind.COUNTERED_SPELL:
        result.append(
            NormalizedZoneTriggerEvent(
                "spell.countered", "before", FrozenMap(common)
            )
        )
        if occurrence.card_object and occurrence.destination == "graveyard":
            result.append(
                NormalizedZoneTriggerEvent(
                    "card.graveyard", "after", FrozenMap(common)
                )
            )
    if occurrence.origin == "battlefield" and occurrence.destination != "battlefield":
        departure = {
            **common,
            "controller": occurrence.previous_controller,
            "types": sorted(previous_types),
        }
        result.append(
            NormalizedZoneTriggerEvent(
                "permanent.leave", "before", FrozenMap(departure)
            )
        )
        if occurrence.destination == "graveyard":
            if "creature" in previous_types:
                result.append(
                    NormalizedZoneTriggerEvent(
                        "creature.dies", "before", FrozenMap(departure)
                    )
                )
            if "artifact" in previous_types:
                result.append(
                    NormalizedZoneTriggerEvent(
                        "artifact.graveyard", "before", FrozenMap(departure)
                    )
                )
            result.append(
                NormalizedZoneTriggerEvent(
                    "permanent.graveyard", "before", FrozenMap(departure)
                )
            )
    if occurrence.origin == "graveyard" and occurrence.destination != "graveyard":
        result.append(
            NormalizedZoneTriggerEvent(
                "card.leave_graveyard", "after", FrozenMap(common)
            )
        )
    if occurrence.origin == "hand" and occurrence.destination == "graveyard":
        result.append(
            NormalizedZoneTriggerEvent(
                "card.discarded", "after", FrozenMap(common)
            )
        )
    if occurrence.destination == "battlefield" and occurrence.origin != "battlefield":
        current_facts = sealed_public_characteristic_facts(
            occurrence.current_characteristics
        )
        current_types = set(current_facts["types"])
        entered = {
            **common,
            "controller": occurrence.current_controller,
            **current_facts,
            "mana_value": float(
                occurrence.current_characteristics.get("mana_value", 0) or 0
            ),
            "tapped": occurrence.tapped,
        }
        result.append(
            NormalizedZoneTriggerEvent(
                "permanent.enter", "after", FrozenMap(entered)
            )
        )
        for card_type in ("artifact", "creature", "land", "enchantment"):
            if card_type in current_types:
                result.append(
                    NormalizedZoneTriggerEvent(
                        f"{card_type}.enter", "after", FrozenMap(entered)
                    )
                )
    return tuple(result)


__all__ = [
    "NormalizedZoneTriggerEvent",
    "ZoneChangeOccurrence",
    "ZoneTransitionKind",
    "ZoneTriggerEventError",
    "normalized_library_position",
    "normalized_transition_kind_map",
    "normalized_zone_trigger_events",
    "sealed_public_characteristic_facts",
    "validate_zone_transition_request",
]
