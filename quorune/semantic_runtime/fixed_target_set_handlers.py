from __future__ import annotations

"""Closed runtime owners for homogeneous fixed target-set effects."""

from dataclasses import dataclass
from typing import Any, Mapping

from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import (
    DestroyPermanentTargetsIntent,
    IntentPlan,
    MoveObjectsSimultaneouslyIntent,
    SetPermanentsTappedIntent,
)


_COMMON_FIELDS = frozenset(
    {
        "op",
        "cards",
        "maximum_targets",
        "reason",
        "_replacement_selections",
    }
)
_FIXED_TARGET_SET_CAPABILITY = "resolution.effect.fixed_homogeneous_target_set"


def _fixed_target_fields(
    effect: Mapping[str, Any],
    context: ReadOnlyHandlerContext,
    *,
    operation: str,
    replacements: bool,
) -> tuple[tuple[str, ...], int, str, tuple[Any, ...]]:
    unknown = sorted(set(effect) - _COMMON_FIELDS)
    if unknown:
        raise SemanticNodeError(
            "Fixed target-set effect has unknown fields: " + ", ".join(unknown)
        )
    if effect.get("op") != operation:
        raise SemanticNodeError("Fixed target-set operation is unsupported")
    raw_cards = effect.get("cards")
    maximum = effect.get("maximum_targets")
    if (
        not isinstance(raw_cards, (list, tuple))
        or any(type(value) is not str or not value for value in raw_cards)
        or len(raw_cards) != len(set(raw_cards))
        or type(maximum) is not int
        or not 1 <= maximum <= 6
        or len(raw_cards) > maximum
    ):
        raise SemanticNodeError(
            "Fixed target-set effects require at most their unique target bound"
        )
    raw_reason = effect.get("reason")
    if raw_reason is not None and (
        type(raw_reason) is not str or not raw_reason
    ):
        raise SemanticNodeError(
            "Fixed target-set reason must be a nonempty string"
        )
    raw_selections = effect.get("_replacement_selections", ())
    if not isinstance(raw_selections, (list, tuple)):
        raise SemanticNodeError(
            "Fixed target-set replacement selections must be an array"
        )
    if raw_selections and not replacements:
        raise SemanticNodeError(
            "Tap-state target sets do not accept replacement selections"
        )
    return (
        tuple(raw_cards),
        maximum,
        raw_reason or context.default_reason,
        tuple(raw_selections),
    )


@dataclass(frozen=True, slots=True)
class DestroyPermanentTargetSetHandler:
    handler_id: str = "generic.destroy-permanent-target-set.v1"
    schema_version: int = 1
    family: str = "effect.permanent-destruction-target-set"
    operation: str = "destroy_targets"
    rule_references: tuple[str, ...] = (
        "608.2b",
        "608.2c",
        "701.8",
        "701.8a",
        "701.8b",
        "701.8c",
        "702.12b",
    )
    capability_dependencies: tuple[str, ...] = (
        "permanent.destroy.effect",
        _FIXED_TARGET_SET_CAPABILITY,
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        cards, _maximum, reason, selections = _fixed_target_fields(
            effect,
            context,
            operation=self.operation,
            replacements=True,
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                DestroyPermanentTargetsIntent(
                    actor=context.actor,
                    object_refs=cards,
                    reason=reason,
                    replacement_selections=selections,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class MoveFixedTargetSetHandler:
    handler_id: str
    family: str
    operation: str
    expected_zone: str
    destination: str
    owned_only: bool
    capability_dependencies: tuple[str, ...]
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "400.2",
        "400.3",
        "400.7",
        "608.2b",
        "608.2c",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        cards, _maximum, reason, selections = _fixed_target_fields(
            effect,
            context,
            operation=self.operation,
            replacements=True,
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                MoveObjectsSimultaneouslyIntent(
                    actor=context.actor,
                    object_refs=cards,
                    expected_zones=(self.expected_zone,),
                    destination=self.destination,
                    reason=reason,
                    owned_only=self.owned_only,
                    replacement_selections=selections,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class SetFixedTargetSetTappedHandler:
    handler_id: str
    operation: str
    tapped: bool
    capability_dependencies: tuple[str, ...]
    schema_version: int = 1
    family: str = "effect.permanent-tap-state-target-set"
    rule_references: tuple[str, ...] = (
        "608.2b",
        "608.2c",
        "701.26",
        "701.26a",
        "701.26b",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        cards, _maximum, reason, _selections = _fixed_target_fields(
            effect,
            context,
            operation=self.operation,
            replacements=False,
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                SetPermanentsTappedIntent(
                    object_refs=cards,
                    actor=context.actor,
                    tapped=self.tapped,
                    reason=reason,
                ),
            ),
        )


FIXED_TARGET_SET_HANDLERS = (
    DestroyPermanentTargetSetHandler(),
    MoveFixedTargetSetHandler(
        handler_id="generic.exile-permanent-target-set.v1",
        family="effect.permanent-exile-target-set",
        operation="exile_permanent_targets",
        expected_zone="battlefield",
        destination="exile",
        owned_only=False,
        capability_dependencies=(
            "permanent.exile.effect",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    MoveFixedTargetSetHandler(
        handler_id="generic.return-permanent-target-set.v1",
        family="effect.permanent-return-target-set",
        operation="return_permanent_targets_to_owner_hand",
        expected_zone="battlefield",
        destination="hand",
        owned_only=False,
        capability_dependencies=(
            "permanent.return.owner_hand",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    MoveFixedTargetSetHandler(
        handler_id="generic.return-graveyard-target-set.v1",
        family="effect.graveyard-card-return-target-set",
        operation="return_graveyard_targets_to_owner_hand",
        expected_zone="graveyard",
        destination="hand",
        owned_only=True,
        capability_dependencies=(
            "card.return.own_graveyard_to_owner_hand",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    MoveFixedTargetSetHandler(
        handler_id="generic.exile-public-graveyard-target-set.v1",
        family="effect.public-graveyard-exile-target-set",
        operation="exile_public_graveyard_targets",
        expected_zone="graveyard",
        destination="exile",
        owned_only=False,
        capability_dependencies=(
            "card.exile.public_graveyard",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    SetFixedTargetSetTappedHandler(
        handler_id="generic.tap-target-set.v1",
        operation="tap_targets",
        tapped=True,
        capability_dependencies=(
            "permanent.tap.effect",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    SetFixedTargetSetTappedHandler(
        handler_id="generic.untap-target-set.v1",
        operation="untap_targets",
        tapped=False,
        capability_dependencies=(
            "permanent.untap.effect",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
)


__all__ = [
    "DestroyPermanentTargetSetHandler",
    "FIXED_TARGET_SET_HANDLERS",
    "MoveFixedTargetSetHandler",
    "SetFixedTargetSetTappedHandler",
]
