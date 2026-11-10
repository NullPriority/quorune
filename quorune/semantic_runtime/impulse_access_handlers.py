from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..compiler.impulse_access_templates import IMPULSE_ACCESS_CAPABILITY_ID
from ..impulse_access_model import ImpulseAccessDuration, IMPULSE_ACCESS_DECLARED_COUNT_VERSION, impulse_access_count_is_legal
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import ImpulseAccessIntent, IntentPlan


@dataclass(frozen=True, slots=True)
class FixedImpulseAccessHandler:
    handler_id: str = "generic.fixed-impulse-access.v1"
    schema_version: int = 1
    family: str = "effect.impulse-access"
    operation: str = "fixed_impulse_access"
    rule_references: tuple[str, ...] = (
        "400.7",
        "406.1",
        "601.2",
        "701.13a",
    )
    capability_dependencies: tuple[str, ...] = (
        IMPULSE_ACCESS_CAPABILITY_ID,
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        allowed = {"op", "player", "count", "duration", "reason", "schema_version"}
        if set(effect) - allowed or not {
            "op",
            "player",
            "count",
            "duration",
        }.issubset(effect):
            raise SemanticNodeError(
                "Fixed impulse-access effect has an invalid shape"
            )
        if effect.get("op") != self.operation:
            raise SemanticNodeError(
                "Fixed impulse-access operation is unsupported"
            )
        count = effect.get("count")
        version = effect.get("schema_version", 1)
        if (not impulse_access_count_is_legal(count, version)
                or ("schema_version" in effect and (version != IMPULSE_ACCESS_DECLARED_COUNT_VERSION
                    or effect["player"] != context.actor))):
            raise SemanticNodeError("Impulse access requires a count and controller valid for its instruction version")
        try:
            duration = ImpulseAccessDuration(str(effect.get("duration") or ""))
        except ValueError as exc:
            raise SemanticNodeError(
                "Fixed impulse access requires a supported duration"
            ) from exc
        if version == IMPULSE_ACCESS_DECLARED_COUNT_VERSION and duration is ImpulseAccessDuration.UNTIL_USED:
            raise SemanticNodeError("Declared impulse access requires its fixed turn duration")
        player = context.query.require_active_seat(
            str(effect.get("player") or context.actor)
        )
        reason = effect.get("reason") or context.default_reason
        if type(reason) is not str or not reason:
            raise SemanticNodeError(
                "Fixed impulse access requires a nonempty reason"
            )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                ImpulseAccessIntent(
                    actor=context.actor,
                    player=player,
                    count=count,
                    duration=duration,
                    reason=reason,
                    schema_version=version,
                ),
            ),
        )


IMPULSE_ACCESS_HANDLERS = (FixedImpulseAccessHandler(),)


__all__ = ["FixedImpulseAccessHandler", "IMPULSE_ACCESS_HANDLERS"]
