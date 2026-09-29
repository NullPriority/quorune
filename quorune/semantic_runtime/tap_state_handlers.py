from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
)
from ..public_tap_state_sets import SetPublicPermanentsTappedIntent
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import (
    IntentPlan,
    SetPermanentTappedIntent,
    UntapAllCreaturesIntent,
)
from .nodes import SetPermanentTappedNode, UntapAllCreaturesNode


_AGGREGATE_TAP_STATE_FIELDS = frozenset(dict(op=None, reason=None))
_REASON_FIELD = next(iter(dict(reason=None)))


def _reason(
    effect: Mapping[str, Any], context: ReadOnlyHandlerContext
) -> str:
    return str(effect.get(_REASON_FIELD) or context.default_reason)


def _source_logical_object_id(
    object_ref: str,
    context: ReadOnlyHandlerContext,
) -> str | None:
    source = context.source
    if source is None or source.card_ref != object_ref:
        return None
    return source.logical_object_id


def _require_fields(
    effect: Mapping[str, Any], allowed: frozenset[str]
) -> None:
    unknown = sorted(set(effect) - allowed)
    if unknown:
        raise SemanticNodeError(
            "Tap-state effect has unknown fields: " + ", ".join(unknown)
        )


@dataclass(frozen=True, slots=True)
class TapPermanentHandler:
    handler_id: str = "generic.tap-permanent.v2"
    schema_version: int = 2
    family: str = "permanent.tap_state"
    operation: str = "tap"
    rule_references: tuple[str, ...] = ("701.26", "701.26a")
    capability_dependencies: tuple[str, ...] = (
        "permanent.tap.effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Tap-state",
            allow_replacement_selections=False,
        )
        object_ref = fields.object_ref
        node = SetPermanentTappedNode(
            object_ref=object_ref,
            tapped=True,
            reason=fields.reason,
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                SetPermanentTappedIntent(
                    object_ref=node.object_ref,
                    actor=context.actor,
                    tapped=node.tapped,
                    reason=node.reason,
                    logical_object_id=_source_logical_object_id(
                        object_ref,
                        context,
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class UntapPermanentHandler:
    handler_id: str = "generic.untap-permanent.v2"
    schema_version: int = 2
    family: str = "permanent.tap_state"
    operation: str = "untap"
    rule_references: tuple[str, ...] = (
        "122.1d",
        "701.26",
        "701.26b",
    )
    capability_dependencies: tuple[str, ...] = (
        "permanent.untap.effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Tap-state",
            allow_replacement_selections=False,
        )
        object_ref = fields.object_ref
        node = SetPermanentTappedNode(
            object_ref=object_ref,
            tapped=False,
            reason=fields.reason,
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                SetPermanentTappedIntent(
                    object_ref=node.object_ref,
                    actor=context.actor,
                    tapped=node.tapped,
                    reason=node.reason,
                    logical_object_id=_source_logical_object_id(
                        object_ref,
                        context,
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class UntapAllCreaturesHandler:
    handler_id: str = "generic.untap-all-creatures.v1"
    schema_version: int = 1
    family: str = "permanent.tap_state"
    operation: str = "untap_all_creatures"
    rule_references: tuple[str, ...] = (
        "110.4",
        "122.1d",
        "701.26",
        "701.26b",
        "702.26b",
    )
    capability_dependencies: tuple[str, ...] = (
        "permanent.untap.all_creatures",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        _require_fields(effect, _AGGREGATE_TAP_STATE_FIELDS)
        node = UntapAllCreaturesNode(reason=_reason(effect, context))
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                UntapAllCreaturesIntent(
                    actor=context.actor,
                    reason=node.reason,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class SetPublicTapStateSetHandler:
    handler_id: str = "generic.set-public-tap-state-set.v1"
    schema_version: int = 1
    family: str = "permanent.tap_state"
    operation: str = "set_public_tap_state"
    rule_references: tuple[str, ...] = (
        "608.2c",
        "701.26",
        "701.26a",
        "701.26b",
    )
    capability_dependencies: tuple[str, ...] = (
        "permanent.tap_state.fixed_set",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        if (
            set(effect) != {"op", "source", "set", "tapped"}
            or effect.get("op") != self.operation
            or type(effect.get("tapped")) is not bool
        ):
            raise SemanticNodeError(
                "Public-set tap-state effect has an invalid shape"
            )
        try:
            spec = AffectedPermanentSetSpec.from_dict(effect["set"])
        except (KeyError, TypeError, AffectedPermanentSetError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if spec.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
            context.query.require_active_seat(
                str(spec.target_controller or "")
            )
        source_ref = effect.get("source")
        if source_ref is not None and (
            type(source_ref) is not str or not source_ref
        ):
            raise SemanticNodeError(
                "Public-set tap-state source must be a nonempty reference"
            )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                SetPublicPermanentsTappedIntent(
                    actor=context.actor,
                    spec=spec,
                    tapped=effect["tapped"],
                    reason=context.default_reason,
                    source_ref=source_ref,
                ),
            ),
        )


TAP_STATE_HANDLERS = (
    TapPermanentHandler(),
    UntapPermanentHandler(),
    UntapAllCreaturesHandler(),
    SetPublicTapStateSetHandler(),
)
