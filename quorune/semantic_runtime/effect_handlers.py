from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..effect_contracts import EffectFamilyContract, effect_operation_contracts
from ..replacement.immutable import FrozenMap
from ..source_tap_effect import RUNTIME_SOURCE_CONTINUITY_FIELD, SOURCE_TAP_EFFECT_FIELD
from ..linked_exile_return_model import LINKED_EXILE_RETURN_CAPABILITY, LINKED_EXILE_RETURN_OPERATION, LINKED_RETURN_OPERATION, validate_linked_instruction
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import DomainEffectIntent, IntentPlan
from ..blocking_requirement_model import BLOCKING_REQUIREMENT_CAPABILITY,BLOCKING_REQUIREMENT_OPERATION
from ..rules.detain import (
    DETAIN_CAPABILITY, DETAIN_OPERATION, DETAIN_RULES, DIRECT_PROHIBITION_CAPABILITY,
    DIRECT_PROHIBITION_OPERATION, DIRECT_PROHIBITION_RULES, validate_detain_instruction,
    validate_direct_action_prohibition,
)
from ..saddle import SADDLE_CAPABILITY, SADDLE_OPERATION


@dataclass(frozen=True, slots=True)
class DomainEffectHandler:
    operation: str
    contract: EffectFamilyContract
    schema_version: int = 1
    capability_dependencies: tuple[str, ...] = ()

    @property
    def handler_id(self) -> str:
        return f"{self.contract.semantic_family}.{self.operation}.v1"

    @property
    def family(self) -> str:
        return self.contract.semantic_family

    @property
    def rule_references(self) -> tuple[str, ...]:
        if self.operation == DETAIN_OPERATION:
            return DETAIN_RULES
        if self.operation == DIRECT_PROHIBITION_OPERATION:
            return DIRECT_PROHIBITION_RULES
        if self.operation=='turn_face_up':
            from ..face_up_effect_model import FACE_UP_EFFECT_RULES
            return FACE_UP_EFFECT_RULES
        if self.operation == SADDLE_OPERATION:
            return ("400.7", "514.2", "702.171b")
        return self.contract.rule_references

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        operation = str(effect.get("op") or "").casefold()
        if operation != self.operation:
            raise SemanticNodeError(
                f"Handler {self.handler_id} cannot lower {operation!r}"
            )
        if operation == 'reanimate' and effect.get('schema_version') == 2:
            from ..event_card_return import event_battlefield_return_intent
            try:
                intent = event_battlefield_return_intent(effect, actor=context.actor, reason=context.default_reason)
                context.query.require_known_seat(intent.new_controller)
            except (ValueError, TypeError, KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(operation=operation, handler_id=self.handler_id, intents=(intent,))
        if operation == 'reanimate' and effect.get('schema_version') == 3:
            from ..reanimation_entry_counters import reanimation_entry_counter_intent
            try:
                intent=reanimation_entry_counter_intent(effect,actor=context.actor,reason=context.default_reason)
                context.query.require_known_seat(intent.new_controller)
            except (ValueError,TypeError,KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(operation=operation,handler_id=self.handler_id,intents=(intent,))
        reason = str(effect.get("reason") or context.default_reason)
        if not reason:
            raise SemanticNodeError("A domain effect requires a reason")
        if "_runtime_source" in effect or RUNTIME_SOURCE_CONTINUITY_FIELD in effect:
            raise SemanticNodeError(
                "Semantic programs cannot supply authoritative runtime source context"
            )
        if operation == SADDLE_OPERATION:
            ref = effect.get("card")
            if set(effect) - {"op", "card", "reason"} or type(ref) is not str or not ref:
                raise SemanticNodeError("Saddle requires a closed bound source")
        runtime_effect = dict(effect)
        if operation in {'schedule_fixed_object_action','resolve_fixed_object_action'}:
            from ..delayed_object_model import validate_delayed_object_instruction
            try:validate_delayed_object_instruction({k:v for k,v in effect.items() if k!='reason'})
            except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        if operation=='turn_face_up':
            from ..face_up_effect_model import validate_face_up_instruction
            try:validate_face_up_instruction(effect)
            except ValueError as exc:raise SemanticNodeError(str(exc)) from exc
        if operation == 'goad' and effect.get('schema_version') == 2:
            from ..goad_instruction import validate_fixed_goad
            try:validate_fixed_goad(effect)
            except (ValueError,TypeError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        if operation in {DETAIN_OPERATION, DIRECT_PROHIBITION_OPERATION}:
            validate = validate_detain_instruction if operation == DETAIN_OPERATION else validate_direct_action_prohibition
            try:
                validate(effect, resolved=True)
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
        if operation == LINKED_EXILE_RETURN_OPERATION:
            try:
                validate_linked_instruction(effect)
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
        if context.source is not None:
            runtime_effect["_runtime_source"] = context.source.to_dict()
            if SOURCE_TAP_EFFECT_FIELD in effect:
                history = context.source.duration_history
                runtime_effect[RUNTIME_SOURCE_CONTINUITY_FIELD] = {
                    "history": history.to_dict() if history is not None else None,
                    "resolution_timestamp": context.source.duration_resolution_timestamp,
                }
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                DomainEffectIntent(
                    actor=context.actor,
                    operation=self.operation,
                    effect=FrozenMap(runtime_effect),
                    reason=reason,
                ),
            ),
        )


def _domain_capabilities(operation):
    if operation == DETAIN_OPERATION:
        return (DETAIN_CAPABILITY,)
    if operation == DIRECT_PROHIBITION_OPERATION:
        return (DIRECT_PROHIBITION_CAPABILITY,)
    if operation in {'schedule_fixed_object_action','resolve_fixed_object_action'}:
        from ..delayed_object_model import DELAYED_OBJECT_CAPABILITY
        return (DELAYED_OBJECT_CAPABILITY,)
    if operation=='set_life_total':return ('life.change.set_total',)
    if operation=='turn_face_up':
        from ..face_up_effect_model import FACE_UP_EFFECT_CAPABILITY
        return (FACE_UP_EFFECT_CAPABILITY,)
    if operation==BLOCKING_REQUIREMENT_OPERATION:return (BLOCKING_REQUIREMENT_CAPABILITY,)
    if operation == SADDLE_OPERATION: return (SADDLE_CAPABILITY,)
    if operation in {LINKED_EXILE_RETURN_OPERATION,LINKED_RETURN_OPERATION}:return (LINKED_EXILE_RETURN_CAPABILITY,)
    return ()


DOMAIN_EFFECT_HANDLERS = tuple(
    DomainEffectHandler(operation=operation, contract=contract, capability_dependencies=_domain_capabilities(operation))
    for operation, contract in effect_operation_contracts()
    if operation not in {"bounce", "destroy", "mill", "create_compiled_emblem"}
)
