from __future__ import annotations

"""Strict typed lowering for resolution-created control effects."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousEffectDuration
from ..continuous_effect_state import ResolutionEffectSource
from ..object_predicate import ObjectQuerySpec
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .control_intents import GainControlIntent, GainControlSetIntent
from .direct_target_fields import validate_direct_target_effect
from .intents import IntentPlan


def _source(context: ReadOnlyHandlerContext) -> ResolutionEffectSource:
    if context.source is None:
        raise SemanticNodeError("Control requires the resolving stack source")
    return ResolutionEffectSource(**context.source.to_dict())


@dataclass(frozen=True, slots=True)
class FixedControlHandler:
    handler_id: str = "generic.fixed-control.v1"
    schema_version: int = 1
    family: str = "effect.control.fixed_resolution"
    operation: str = "gain_control"
    rule_references: tuple[str, ...] = ("302.6", "506.4", "608.2c", "611.2a", "611.2b", "611.2c", "613.1b", "613.7", "613.8a", "613.8b", "613.8c", "800.4a")
    capability_dependencies: tuple[str, ...] = ("continuous.control.fixed_resolution",)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        fields = validate_direct_target_effect(
            effect, context, operation=self.operation, reference_field="card",
            family_label="Control", allow_replacement_selections=False,
            additional_allowed_fields=("controller", "duration", "source", "duration_source"),
        )
        try:
            duration = ContinuousEffectDuration(effect.get("duration"))
            source = _source(context)
            if type(effect.get("source")) is not str or not effect["source"]:
                raise ValueError("Control requires source provenance")
            if effect.get("controller") != context.actor:
                raise ValueError("Control must be acquired by its resolving controller")
            if duration.source_bound:
                if "duration_source" not in effect or effect["duration_source"] not in {None, source.card_ref}:
                    raise ValueError("Control duration must track the original source")
            elif "duration_source" in effect:
                raise ValueError("This control duration cannot track another source")
            intent = GainControlIntent(
                actor=context.actor, object_ref=fields.object_ref, controller=context.actor,
                duration=duration, source=source, reason=fields.reason,
                history_snapshot=context.source.duration_history,
                resolution_timestamp=context.source.duration_resolution_timestamp,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(self.operation, self.handler_id, (intent,))


@dataclass(frozen=True, slots=True)
class FixedControlSetHandler:
    handler_id: str = "generic.fixed-control-set.v1"
    schema_version: int = 1
    family: str = "effect.control.fixed_resolution_set"
    operation: str = "gain_control_set"
    rule_references: tuple[str, ...] = ("502.3", "608.2c", "611.2a", "611.2c", "613.1b", "613.7", "800.4a")
    capability_dependencies: tuple[str, ...] = ("continuous.control.fixed_resolution",)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        required = {"op", "predicate", "controller", "duration", "source", "steps"}
        if not required.issubset(effect) or set(effect) - required - {"reason"} or effect["op"] != self.operation:
            raise SemanticNodeError("Control-set fields are missing or unknown")
        try:
            if effect["controller"] != context.actor:
                raise ValueError("Control-set controller must be its resolving controller")
            if type(effect["source"]) is not str or not effect["source"]:
                raise ValueError("Control-set source provenance is unavailable")
            if not isinstance(effect["steps"], (list, tuple)):
                raise ValueError("Control-set steps must be an array")
            intent = GainControlSetIntent(
                actor=context.actor, predicate=ObjectQuerySpec.from_dict(effect["predicate"]),
                controller=context.actor, duration=ContinuousEffectDuration(effect["duration"]),
                source=_source(context), steps=tuple(effect["steps"]),
                reason=effect.get("reason", context.default_reason),
            )
            from ..control_effects import control_set_query_is_closed
            if not control_set_query_is_closed(intent.predicate, actor=context.actor):
                raise ValueError("Control-set query is outside its public closed family")
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(self.operation, self.handler_id, (intent,))


CONTROL_HANDLERS = (FixedControlHandler(), FixedControlSetHandler())
