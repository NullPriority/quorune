from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any, Mapping, TypeAlias

from .util import stable_json


class TriggerParticipationError(ValueError):
    """A compiled static trigger-participation value is malformed."""


class TriggerMultiplierPredicate(str, Enum):
    """Closed represented CR 603.2d participation predicates."""

    ARTIFACT_OR_CREATURE_ENTERS = "artifact_or_creature_enters"
    ANOTHER_CREATURE_OF_CHOSEN_TYPE = "another_creature_of_chosen_type"


_MULTIPLIER_CAPABILITIES = {
    TriggerMultiplierPredicate.ARTIFACT_OR_CREATURE_ENTERS: (
        "trigger.multiplier.artifact_or_creature_enters"
    ),
    TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE: (
        "trigger.multiplier.another_creature_of_chosen_type"
    ),
}


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise TriggerParticipationError(f"{field} must be a nonempty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class TriggerMultiplierSpec:
    """One typed static effect that makes a represented ability trigger again."""

    predicate: TriggerMultiplierPredicate
    additional_count: int = 1
    exclude_self: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise TriggerParticipationError(
                "Unsupported trigger-multiplier schema version"
            )
        if not isinstance(self.predicate, TriggerMultiplierPredicate):
            raise TriggerParticipationError(
                "Unsupported trigger-multiplier predicate"
            )
        if type(self.additional_count) is not int or self.additional_count <= 0:
            raise TriggerParticipationError(
                "Trigger-multiplier additional_count must be positive"
            )
        if type(self.exclude_self) is not bool:
            raise TriggerParticipationError(
                "Trigger-multiplier exclude_self must be a boolean"
            )
        required_exclusion = (
            self.predicate
            is TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE
        )
        if self.exclude_self is not required_exclusion:
            raise TriggerParticipationError(
                "Trigger-multiplier self exclusion disagrees with its predicate"
            )

    @property
    def capability_id(self) -> str:
        return _MULTIPLIER_CAPABILITIES[self.predicate]

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return ("603.2d",)

    @property
    def requires_chosen_creature_type(self) -> bool:
        return (
            self.predicate
            is TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "predicate": self.predicate.value,
            "additional_count": self.additional_count,
            "exclude_self": self.exclude_self,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TriggerMultiplierSpec":
        expected = {
            "schema_version",
            "predicate",
            "additional_count",
            "exclude_self",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise TriggerParticipationError(
                "Trigger-multiplier fragments have a closed schema"
            )
        try:
            predicate = TriggerMultiplierPredicate(value["predicate"])
        except (TypeError, ValueError) as exc:
            raise TriggerParticipationError(
                "Unsupported trigger-multiplier predicate"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            predicate=predicate,
            additional_count=value["additional_count"],
            exclude_self=value["exclude_self"],
        )


@dataclass(frozen=True, slots=True)
class WardSpec:
    """One represented fixed public Ward ability (CR 702.21)."""

    generic_cost: int | None = None
    life_payment: int | None = None
    discard_cards: int = 0
    schema_version: int | None = None

    def __post_init__(self) -> None:
        kinds = sum(
            (
                self.generic_cost is not None,
                self.life_payment is not None,
                self.discard_cards != 0,
            )
        )
        if kinds != 1:
            raise TriggerParticipationError(
                "Ward requires exactly one represented payment kind"
            )
        schema_version = self.schema_version
        if schema_version is None:
            schema_version = 1 if self.generic_cost is not None else 2
            object.__setattr__(self, "schema_version", schema_version)
        if type(schema_version) is not int or schema_version not in {1, 2}:
            raise TriggerParticipationError("Unsupported Ward schema version")
        if self.generic_cost is not None and (
            type(self.generic_cost) is not int or self.generic_cost < 0
        ):
            raise TriggerParticipationError(
                "Ward generic_cost must be a nonnegative integer"
            )
        if self.life_payment is not None and (
            type(self.life_payment) is not int or self.life_payment <= 0
        ):
            raise TriggerParticipationError(
                "Ward life_payment must be a positive integer"
            )
        if type(self.discard_cards) is not int or self.discard_cards not in {
            0,
            1,
        }:
            raise TriggerParticipationError(
                "Ward discard_cards must be zero or one"
            )
        if schema_version == 1 and (
            self.generic_cost is None
            or self.life_payment is not None
            or self.discard_cards
        ):
            raise TriggerParticipationError(
                "Ward schema version 1 is fixed-generic only"
            )
        if schema_version == 2 and self.generic_cost is not None:
            raise TriggerParticipationError(
                "Ward schema version 2 is fixed nonmana only"
            )

    @property
    def payment_kind(self) -> str:
        if self.generic_cost is not None:
            return "mana"
        if self.life_payment is not None:
            return "life"
        return "discard"

    @property
    def capability_id(self) -> str:
        return (
            "trigger.keyword.ward.fixed_generic"
            if self.generic_cost is not None
            else "trigger.keyword.ward.fixed_nonmana"
        )

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return ("603.3", "702.21", "702.21a")

    def to_dict(self) -> dict[str, Any]:
        if self.schema_version == 1:
            return {
                "schema_version": 1,
                "generic_cost": self.generic_cost,
            }
        return {
            "schema_version": 2,
            "generic_cost": None,
            "life_payment": self.life_payment,
            "discard_cards": self.discard_cards,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WardSpec":
        if not isinstance(value, Mapping):
            raise TriggerParticipationError(
                "Ward fragments have a closed schema"
            )
        if value.get("schema_version") == 1 and set(value) == {
            "schema_version",
            "generic_cost",
        }:
            return cls(
                schema_version=1,
                generic_cost=value["generic_cost"],
            )
        if value.get("schema_version") == 2 and set(value) == {
            "schema_version",
            "generic_cost",
            "life_payment",
            "discard_cards",
        }:
            return cls(
                schema_version=2,
                generic_cost=value["generic_cost"],
                life_payment=value["life_payment"],
                discard_cards=value["discard_cards"],
            )
        raise TriggerParticipationError(
            "Ward fragments have a closed schema"
        )


StaticTriggerParticipationSpec: TypeAlias = TriggerMultiplierSpec | WardSpec


@dataclass(frozen=True, slots=True)
class StaticTriggerParticipation:
    """Effective battlefield participation snapshot for a static trigger rule.

    This is deliberately separate from the compiled fragment.  It freezes the
    physical object, current logical incarnation, controller, chosen value,
    and effective ability presence used by one trigger-discovery transaction.
    """

    source_object_id: str
    source_logical_object_id: str
    source_controller: str
    active_zone: str
    spec: StaticTriggerParticipationSpec
    chosen_creature_type: str | None = None
    effective_ability_present: bool = True
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise TriggerParticipationError(
                "Unsupported static trigger-participation schema version"
            )
        for field in (
            "source_object_id",
            "source_logical_object_id",
            "source_controller",
            "active_zone",
        ):
            object.__setattr__(
                self,
                field,
                _nonempty(getattr(self, field), field=field),
            )
        if self.active_zone != "battlefield":
            raise TriggerParticipationError(
                "Static trigger participation is battlefield-only"
            )
        if not isinstance(self.spec, (TriggerMultiplierSpec, WardSpec)):
            raise TriggerParticipationError(
                "Unsupported static trigger-participation spec"
            )
        if type(self.effective_ability_present) is not bool:
            raise TriggerParticipationError(
                "effective_ability_present must be a boolean"
            )
        chosen = self.chosen_creature_type
        if chosen is not None:
            chosen = _nonempty(chosen, field="chosen_creature_type").casefold()
            object.__setattr__(self, "chosen_creature_type", chosen)
        requires_choice = (
            isinstance(self.spec, TriggerMultiplierSpec)
            and self.spec.requires_chosen_creature_type
        )
        if requires_choice is not bool(chosen):
            raise TriggerParticipationError(
                "Chosen creature type presence disagrees with the static spec"
            )
        if isinstance(self.spec, WardSpec) and chosen is not None:
            raise TriggerParticipationError(
                "Ward participation cannot carry a chosen creature type"
            )

    @property
    def capability_id(self) -> str:
        return self.spec.capability_id

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return self.spec.rule_ids

    def to_dict(self) -> dict[str, Any]:
        kind = "trigger_multiplier" if isinstance(
            self.spec, TriggerMultiplierSpec
        ) else "ward"
        return {
            "schema_version": self.schema_version,
            "source_object_id": self.source_object_id,
            "source_logical_object_id": self.source_logical_object_id,
            "source_controller": self.source_controller,
            "active_zone": self.active_zone,
            "spec": {"kind": kind, "value": self.spec.to_dict()},
            "chosen_creature_type": self.chosen_creature_type,
            "effective_ability_present": self.effective_ability_present,
            "capability_id": self.capability_id,
            "rule_ids": list(self.rule_ids),
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


__all__ = [
    "StaticTriggerParticipation",
    "StaticTriggerParticipationSpec",
    "TriggerMultiplierPredicate",
    "TriggerMultiplierSpec",
    "TriggerParticipationError",
    "WardSpec",
]
