from __future__ import annotations

"""Typed resolution-created declaration rules stored in the effect journal."""

from dataclasses import dataclass
from typing import Any, Mapping, TypeAlias

from .continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectError,
    ContinuousObjectIdentity,
)
from .declaration_fragments import DeclarationRestrictionTemplate
from .replacement.immutable import immutable_fingerprint
from .object_predicate import ObjectQuerySpec
from .as_unblocked_rule import AS_UNBLOCKED_RULE_KIND,AsUnblockedAssignmentRule
from .toughness_assignment_rule import TOUGHNESS_RULE_KIND,ResolvedToughnessAssignmentRule


RESOLUTION_DECLARATION_RULE_EFFECT_KIND = "resolution_declaration_rule"


def declaration_group_query_is_closed(query: ObjectQuerySpec, *, resolved: bool = False) -> bool:
    """Keep live game-rule recipients on current public creature predicates."""
    return bool(
        isinstance(query, ObjectQuerySpec)
        and query.zones == ("battlefield",)
        and "creature" in query.types_all
        and query.owner is None
        and (resolved or query.controller is None and not query.excluded_controllers)
        and (query.controller is None or not query.controller.startswith("$"))
        and all(not controller.startswith("$") for controller in query.excluded_controllers)
        and not query.include_phased_out
        and query.known_to_actor is None
        and query.exclude_ref is None
    )


@dataclass(frozen=True, slots=True)
class ResolutionDeclarationRuleEffect:
    """One duration-bound declaration rule created by resolution.

    The affected object supplies the source-relative anchor used by the
    declaration solver, but the rule is not an ability or characteristic of
    that object.  Physical and logical identity keep the rule on the exact
    battlefield incarnation selected when it resolved. A live group query
    instead tests current public membership, including later entrants.
    """

    effect_id: str
    source_id: str
    timestamp: int
    restriction: DeclarationRestrictionTemplate
    locked_objects: tuple[ContinuousObjectIdentity, ...]
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    )
    live_query: ObjectQuerySpec | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("effect_id", self.effect_id),
            ("source_id", self.source_id),
        ):
            if type(value) is not str or not value:
                raise ContinuousEffectError(
                    f"Declaration-rule effect {name} must be a nonempty string"
                )
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ContinuousEffectError(
                "Declaration-rule effect timestamps must be nonnegative integers"
            )
        if not isinstance(self.restriction, DeclarationRestrictionTemplate):
            raise ContinuousEffectError(
                "Declaration-rule effects require a typed restriction"
            )
        if (
            self.restriction.mode != "prohibit"
            or self.restriction.scope not in {"self", "source_option"}
        ):
            raise ContinuousEffectError(
                "Resolution-created declaration rules require an "
                "object-anchored prohibition"
            )
        try:
            duration = ContinuousEffectDuration(self.duration)
        except (TypeError, ValueError) as exc:
            raise ContinuousEffectError(
                "Declaration-rule effect duration is invalid"
            ) from exc
        if duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:
            raise ContinuousEffectError(
                "Represented resolution-created declaration rules expire "
                "at end of turn"
            )
        object.__setattr__(self, "duration", duration)
        try:
            locked = tuple(sorted(tuple(self.locked_objects)))
        except (TypeError, AttributeError) as exc:
            raise ContinuousEffectError(
                "Declaration-rule locked identities must be unique typed values"
            ) from exc
        if (
            (not locked and self.live_query is None)
            or len(set(locked)) != len(locked)
            or not all(
                isinstance(identity, ContinuousObjectIdentity)
                for identity in locked
            )
        ):
            raise ContinuousEffectError(
                "Declaration-rule locked identities must be unique typed values"
            )
        object.__setattr__(self, "locked_objects", locked)
        if self.live_query is not None and (
            locked or not declaration_group_query_is_closed(self.live_query, resolved=True)
        ):
            raise ContinuousEffectError("Live declaration rules require one public creature query and no locked objects")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "effect_kind": RESOLUTION_DECLARATION_RULE_EFFECT_KIND,
            "effect_id": self.effect_id,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "restriction": self.restriction.to_dict(),
            "duration": self.duration.value,
            "locked_objects": [
                identity.to_dict() for identity in self.locked_objects
            ],
        }
        if self.live_query is not None:
            value.update(schema_version=2, live_query=self.live_query.to_dict())
        return value

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "ResolutionDeclarationRuleEffect":
        expected = {
            "effect_kind",
            "effect_id",
            "source_id",
            "timestamp",
            "restriction",
            "duration",
            "locked_objects",
        }
        live = isinstance(value, Mapping) and set(value) == expected | {"schema_version", "live_query"}
        if not isinstance(value, Mapping) or (set(value) != expected and not live):
            raise ContinuousEffectError(
                "Declaration-rule effect fields are missing or unknown"
            )
        if value["effect_kind"] != RESOLUTION_DECLARATION_RULE_EFFECT_KIND:
            raise ContinuousEffectError(
                "Declaration-rule effect kind is unsupported"
            )
        if live and (type(value["schema_version"]) is not int or value["schema_version"] != 2 or not isinstance(value["live_query"], Mapping)):
            raise ContinuousEffectError("Live declaration-rule schema version and query are malformed")
        if not isinstance(value["restriction"], Mapping) or not isinstance(
            value["locked_objects"], list
        ):
            raise ContinuousEffectError(
                "Declaration-rule restriction and locked objects are malformed"
            )
        try:
            return cls(
                effect_id=value["effect_id"],
                source_id=value["source_id"],
                timestamp=value["timestamp"],
                restriction=DeclarationRestrictionTemplate.from_dict(
                    value["restriction"]
                ),
                duration=ContinuousEffectDuration(value["duration"]),
                locked_objects=tuple(
                    ContinuousObjectIdentity.from_dict(identity)
                    for identity in value["locked_objects"]
                ),
                live_query=ObjectQuerySpec.from_dict(value["live_query"]) if live else None,
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ContinuousEffectError):
                raise
            raise ContinuousEffectError(str(exc)) from exc

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())


ContinuousJournalEffect: TypeAlias = (
    ContinuousEffect | ResolutionDeclarationRuleEffect | AsUnblockedAssignmentRule | ResolvedToughnessAssignmentRule
)


def continuous_journal_effect_from_dict(
    value: Mapping[str, Any],
) -> ContinuousJournalEffect:
    """Deserialize additive journal records while preserving legacy effects."""

    if isinstance(value, Mapping) and "effect_kind" in value:
        if value['effect_kind']==AS_UNBLOCKED_RULE_KIND:return AsUnblockedAssignmentRule.from_dict(value)
        if value['effect_kind']==TOUGHNESS_RULE_KIND:return ResolvedToughnessAssignmentRule.from_dict(value)
        return ResolutionDeclarationRuleEffect.from_dict(value)
    return ContinuousEffect.from_dict(value)


__all__ = [
    "ContinuousJournalEffect",
    "RESOLUTION_DECLARATION_RULE_EFFECT_KIND",
    "ResolutionDeclarationRuleEffect",
    "continuous_journal_effect_from_dict",
]
