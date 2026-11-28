from __future__ import annotations

"""Strict typed lowering for resolution-created control effects."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousEffectDuration
from ..continuous_effect_state import ResolutionEffectSource
from ..object_predicate import ObjectQuerySpec
from ..control_exchange_model import (
    PERMANENT_CONTROL_EXCHANGE_DEPENDENCIES, PERMANENT_CONTROL_EXCHANGE_OPERATION,
    decode_permanent_control_exchange,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .control_intents import (
    DirectionalControlIntent,
    GainControlIntent, GainControlSetIntent, GainSpellControlIntent,
    ExchangePermanentControlIntent, GiveControlIntent, GiveControlSetIntent, RestoreOwnerControlIntent,
)
from ..stack_target_change_model import STACK_SPELL_CONTROL_FIELD, validate_stack_spell_control_effect
from .direct_target_fields import validate_direct_target_effect
from .intents import IntentPlan


def _source(context: ReadOnlyHandlerContext) -> ResolutionEffectSource:
    if context.source is None:
        raise SemanticNodeError("Control requires the resolving stack source")
    return ResolutionEffectSource(**context.source.to_dict())


def _spell_control_intent(effect, context):
    try:
        validate_stack_spell_control_effect(effect, resolved=True)
        if effect["controller"] != context.actor:
            raise ValueError("Spell control must be acquired by the resolving controller")
        return GainSpellControlIntent(
            context.actor, effect["stack"], _source(context), effect.get("reason", context.default_reason),
        )
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedControlHandler:
    handler_id: str = "generic.fixed-control.v1"
    schema_version: int = 1
    family: str = "effect.control.fixed_resolution"
    operation: str = "gain_control"
    rule_references: tuple[str, ...] = ("302.6", "506.4", "608.2c", "611.2a", "611.2b", "611.2c", "613.1b", "613.7", "613.8a", "613.8b", "613.8c", "800.4a")
    capability_dependencies: tuple[str, ...] = ("continuous.control.fixed_resolution",)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        if STACK_SPELL_CONTROL_FIELD in effect:
            return IntentPlan(self.operation, self.handler_id, (_spell_control_intent(effect, context),))
        from ..rules.artifact_mana_tap_control import DURATION, bound_artifact_mana_control_identity, uses_artifact_mana_tap_control

        if uses_artifact_mana_tap_control(effect):
            try:
                identity = bound_artifact_mana_control_identity(effect, context.actor)
                intent = GainControlIntent(context.actor, effect["card"], context.actor, DURATION,
                                           _source(context), context.default_reason, recipient_identity=identity)
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(self.operation, self.handler_id, (intent,))
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
        from ..recipient_control_model import (
            TWO_CONTROLLER_CREATURE_TRANSFER_FIELD, two_controller_creature_transfer_query,
            validate_two_controller_creature_transfer,
        )
        from ..rules.directional_control import FIELD as DIRECTIONAL_CONTROL_FIELD, validate_directional_control_result

        if DIRECTIONAL_CONTROL_FIELD in effect:
            try:
                direction = validate_directional_control_result(effect)
                intent = DirectionalControlIntent(context.actor, direction, _source(context), context.default_reason)
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(self.operation, self.handler_id, (intent,))

        from ..rules.targeted_control_set import (
            TARGETED_CONTROL_SET_RECEIVERS, TARGETED_CONTROL_SET_STEPS, TARGETED_CONTROL_SET_VERSION,
            validate_targeted_control_set,
        )

        from ..rules.attached_temporary_control import (
            ATTACHED_CONTROL_RECEIVERS, ATTACHED_TEMPORARY_CONTROL_STEPS, ATTACHED_TEMPORARY_CONTROL_VERSION,
            validate_attached_temporary_control,
        )

        if ATTACHED_TEMPORARY_CONTROL_VERSION in effect:
            try:
                attachment = validate_attached_temporary_control(effect, actor=context.actor, resolved=True)
                if attachment is None:
                    return IntentPlan(self.operation, self.handler_id, ())
                intent = GainControlSetIntent(
                    actor=context.actor, predicate=ATTACHED_CONTROL_RECEIVERS, controller=context.actor,
                    duration=ContinuousEffectDuration.UNTIL_END_OF_TURN, source=_source(context),
                    steps=ATTACHED_TEMPORARY_CONTROL_STEPS, reason=effect.get("reason", context.default_reason),
                    target_refs=(attachment,),
                )
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(self.operation, self.handler_id, (intent,))
        if TARGETED_CONTROL_SET_VERSION in effect:
            try:
                selected = validate_targeted_control_set(effect, resolved=True)
                if effect["controller"] != context.actor:
                    raise ValueError("Announced creature control requires its resolving controller")
                intent = GainControlSetIntent(
                    actor=context.actor, predicate=TARGETED_CONTROL_SET_RECEIVERS, controller=context.actor,
                    duration=ContinuousEffectDuration.UNTIL_END_OF_TURN, source=_source(context),
                    steps=TARGETED_CONTROL_SET_STEPS, reason=effect.get("reason", context.default_reason),
                    target_refs=selected,
                )
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(self.operation, self.handler_id, (intent,))
        if TWO_CONTROLLER_CREATURE_TRANSFER_FIELD in effect:
            try:
                original = _source(context)
                opponent = validate_two_controller_creature_transfer(
                    effect, resolved_source=context.source.card_ref or context.source.stack_ref,
                )
                context.query.require_active_seat(opponent)
                intent = GainControlSetIntent(
                    actor=context.actor, predicate=two_controller_creature_transfer_query(),
                    controller=context.actor, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                    source=original, steps=("untap", "gain_control", "haste"),
                    reason=context.default_reason, opponent=opponent,
                )
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(self.operation, self.handler_id, (intent,))
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


@dataclass(frozen=True,slots=True)
class RecipientControlHandler:
    handler_id: str='generic.fixed-recipient-control.v1'
    schema_version: int=1
    family: str='effect.control.fixed_recipient'
    operation: str='give_control'
    rule_references: tuple[str,...]=('110.2','611.2a','613.1b','613.7','800.4a')
    capability_dependencies: tuple[str,...]=('continuous.control.fixed_recipient',)

    def lower(self,effect,context):
        from ..recipient_control_model import validate_recipient_control_effect
        try:
            if effect.get("schema_version") == 5:
                from ..rules.source_control_reflexive import source_control_reflexive_parts

                source_control_reflexive_parts(effect, allow_reason=True, resolved=True)
            else:
                validate_recipient_control_effect(effect,allow_reason=True,resolved=True)
            recipient=effect['controller'];card=effect['card']
            if recipient is not None:context.query.require_known_seat(recipient)
            source=_source(context)
            if card is None or recipient is None or recipient not in context.query.active_seats:
                return IntentPlan(self.operation,self.handler_id,(),result_shape='by_player')
            from .control_intents import ReflexiveSourceControlIntent

            intent_type = ReflexiveSourceControlIntent if effect["schema_version"] == 5 else GiveControlIntent
            intent = intent_type(context.actor, card, recipient, source, effect.get("reason", context.default_reason))
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(self.operation,self.handler_id,(intent,))


@dataclass(frozen=True,slots=True)
class OwnerControlHandler:
    handler_id: str='generic.fixed-owner-control.v1'
    schema_version: int=1
    family: str='effect.control.fixed_owner_restoration'
    operation: str='restore_owner_control'
    rule_references: tuple[str,...]=('110.2','111.2','608.2f','611.2a','613.1b','613.7b','800.4a')
    capability_dependencies: tuple[str,...]=('continuous.control.fixed_owner_restoration',)

    def lower(self,effect,context):
        from ..owner_control_model import validate_owner_control_effect
        try:
            spec=validate_owner_control_effect(effect,allow_reason=True)
            intent=RestoreOwnerControlIntent(context.actor,spec,_source(context),effect.get('reason',context.default_reason))
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(self.operation,self.handler_id,(intent,),result_shape='by_player')


@dataclass(frozen=True,slots=True)
class RecipientControlSetHandler:
    handler_id: str='generic.fixed-recipient-control-set.v1'
    schema_version: int=1
    family: str='effect.control.fixed_recipient_set'
    operation: str='give_control_set'
    rule_references: tuple[str,...]=('113.7a','400.7','608.2b','611.2a','611.2c','613.1b','613.7b')
    capability_dependencies: tuple[str,...]=('continuous.control.fixed_recipient',)

    def lower(self,effect,context):
        from ..recipient_control_model import validate_recipient_control_set_effect
        try:
            validate_recipient_control_set_effect(effect,allow_reason=True)
            recipient=effect['controller'];source=_source(context)
            if recipient is not None:context.query.require_known_seat(recipient)
            if recipient is None or recipient not in context.query.active_seats:
                return IntentPlan(self.operation,self.handler_id,(),result_shape='by_player')
            intent=GiveControlSetIntent(context.actor,recipient,effect['exclude_source'],source,effect.get('reason',context.default_reason))
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(self.operation,self.handler_id,(intent,),result_shape='by_player')


@dataclass(frozen=True, slots=True)
class PermanentControlExchangeHandler:
    handler_id: str = "generic.permanent-control-exchange.v1"
    schema_version: int = 1
    family: str = "effect.control.permanent_exchange"
    operation: str = PERMANENT_CONTROL_EXCHANGE_OPERATION
    rule_references: tuple[str, ...] = ("608.2b", "611.2a", "613.1b", "701.12a", "701.12b", "800.4a")
    capability_dependencies: tuple[str, ...] = PERMANENT_CONTROL_EXCHANGE_DEPENDENCIES

    def lower(self, effect, context):
        try:
            _scope, references = decode_permanent_control_exchange(effect, resolved=True)
            intents = () if None in references else (
                ExchangePermanentControlIntent(context.actor, references, _source(context),
                    effect.get("reason", context.default_reason)),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(self.operation, self.handler_id, intents, result_shape="by_player")


CONTROL_HANDLERS = (
    FixedControlHandler(), FixedControlSetHandler(), RecipientControlHandler(), OwnerControlHandler(),
    RecipientControlSetHandler(), PermanentControlExchangeHandler(),
)
