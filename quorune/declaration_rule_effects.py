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
from .as_unblocked_rule import AS_UNBLOCKED_RULE_KIND,AsUnblockedAssignmentRule
from .toughness_assignment_rule import TOUGHNESS_RULE_KIND,ResolvedToughnessAssignmentRule


RESOLUTION_DECLARATION_RULE_EFFECT_KIND = "resolution_declaration_rule"


@dataclass(frozen=True, slots=True)
class ResolutionDeclarationRuleEffect:
    """One duration-bound declaration rule created by resolution.

    The affected object supplies the source-relative anchor used by the
    declaration solver, but the rule is not an ability or characteristic of
    that object.  Physical and logical identity keep the rule on the exact
    battlefield incarnation selected when it resolved.
    """

    effect_id: str
    source_id: str
    timestamp: int
    restriction: DeclarationRestrictionTemplate
    locked_objects: tuple[ContinuousObjectIdentity, ...]
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    )

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
            not locked
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

    def to_dict(self) -> dict[str, Any]:
        return {
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
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ContinuousEffectError(
                "Declaration-rule effect fields are missing or unknown"
            )
        if value["effect_kind"] != RESOLUTION_DECLARATION_RULE_EFFECT_KIND:
            raise ContinuousEffectError(
                "Declaration-rule effect kind is unsupported"
            )
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
