from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.controller_private_top_exile_model import (
    CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY, CONTROLLER_PRIVATE_TOP_EXILE_OPERATION,
    CONTROLLER_PRIVATE_TOP_EXILE_RULES, ControllerPrivateTopExile,
)
from ..effect_contracts import EffectFamilyContract, effect_operation_contracts
from ..opponent_library_exile_model import (
    EACH_OPPONENT_TOP_EXILE_CAPABILITY, EACH_OPPONENT_TOP_EXILE_OPERATION,
    OPPONENT_LIBRARY_EXILE_PLAY_CAPABILITY, OPPONENT_LIBRARY_EXILE_PLAY_OPERATION,
    validate_each_opponent_top_exile, validate_opponent_library_exile_play,
)
from ..reanimation_entry_counters import REANIMATION_ENTRY_COUNTER_VERSIONS
from ..replacement.immutable import FrozenMap
from ..source_tap_effect import RUNTIME_SOURCE_CONTINUITY_FIELD, SOURCE_TAP_EFFECT_FIELD
from ..source_control_phase_in_model import SOURCE_CONTROL_PHASE_OUT_SUBJECT, uses_source_control_phase_out
from ..linked_exile_return_model import LINKED_EXILE_RETURN_CAPABILITY, LINKED_EXILE_RETURN_OPERATION, LINKED_RETURN_OPERATION, validate_linked_instruction
from ..rules.linked_loyalty_exile_model import (
    LINKED_LOYALTY_EXILE_FIELD, linked_loyalty_entry_identity,
    validate_linked_loyalty_exile_instruction,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import DomainEffectIntent, IntentPlan
from ..blocking_requirement_model import BLOCKING_REQUIREMENT_CAPABILITY,BLOCKING_REQUIREMENT_OPERATION
from ..rules.detain import (
    DETAIN_CAPABILITY, DETAIN_OPERATION, DETAIN_RULES, DIRECT_PROHIBITION_CAPABILITY,
    DIRECT_PROHIBITION_OPERATION, DIRECT_PROHIBITION_RULES, validate_detain_instruction,
    validate_direct_action_prohibition,
)
from ..saddle import SADDLE_CAPABILITY, SADDLE_OPERATION
from ..rules.player_life_exchange import (
    PLAYER_LIFE_EXCHANGE_CAPABILITY, PLAYER_LIFE_EXCHANGE_OPERATION, PLAYER_LIFE_EXCHANGE_RULES,
)
from ..rules.characteristic_life_exchange import (
    CHARACTERISTIC_LIFE_EXCHANGE_CAPABILITY, CHARACTERISTIC_LIFE_EXCHANGE_OPERATION,
    CHARACTERISTIC_LIFE_EXCHANGE_RULES,
)
from ..rules.cast_cascade_grants import CAST_CASCADE_CAPABILITY, CAST_CASCADE_OPERATION, CAST_CASCADE_RULES
from ..rules.next_spell_payment import NEXT_SPELL_PAYMENT_CAPABILITY, NEXT_SPELL_PAYMENT_OPERATION
from ..phasing_model import (
    PHASE_OUT_CAPABILITY, PHASE_OUT_OPERATION, PHASE_OUT_RULES, PHASING_CAPABILITY, validate_phase_out_effect,
)


def _validate_linked_exile_return_instruction(effect: Mapping[str, Any], *, actor: str) -> None:
    from ..rules.linked_creature_settlement import FIELD, resolved_instruction

    if FIELD in effect:
        resolved_instruction(effect)
        return
    from ..rules.linked_death_upkeep import FIELD as DEATH_UPKEEP_FIELD, resolved_instruction as death_upkeep_instruction

    if DEATH_UPKEEP_FIELD in effect:
        death_upkeep_instruction(effect)
        return
    if LINKED_LOYALTY_EXILE_FIELD not in effect:
        validate_linked_instruction(effect)
        return
    instruction = {key: value for key, value in effect.items() if key != "_replacement_selections"}
    if instruction[LINKED_LOYALTY_EXILE_FIELD] == "enter":
        linked_loyalty_entry_identity(instruction)
    else:
        validate_linked_loyalty_exile_instruction(instruction, actor=actor)


def _validate_library_exile_domain_effect(operation, effect, context):
    material = {key: value for key, value in effect.items() if key != "reason"}
    if operation == CONTROLLER_PRIVATE_TOP_EXILE_OPERATION:
        validate = ControllerPrivateTopExile.from_instruction
        message = "Private controller-library exile requires a resolving spell"
    else:
        validate = (validate_each_opponent_top_exile if operation == EACH_OPPONENT_TOP_EXILE_OPERATION
                    else validate_opponent_library_exile_play)
        message = "Opponent-library exile instructions require a resolving stack object"
    try:
        validate(material, actor=context.actor)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    if context.source is None or not context.source.stack_ref:
        raise SemanticNodeError(message)


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
        if self.operation == CONTROLLER_PRIVATE_TOP_EXILE_OPERATION:
            return CONTROLLER_PRIVATE_TOP_EXILE_RULES
        if self.operation == EACH_OPPONENT_TOP_EXILE_OPERATION:
            return ("101.4", "400.7", "406.3", "608.2c", "609.3", "701.13a")
        if self.operation == OPPONENT_LIBRARY_EXILE_PLAY_OPERATION:
            return ("305.1", "400.7", "406.3", "406.3a", "603.3a", "609.4b", "611.2a", "514.2")
        if self.operation=='grant_next_spell_cost_reduction':
            from ..next_spell_cost_model import RULES
            return RULES
        if self.operation=='set_day_night':return ('731.1','731.1a','702.145')
        if self.operation == PLAYER_LIFE_EXCHANGE_OPERATION:
            return PLAYER_LIFE_EXCHANGE_RULES
        if self.operation == CHARACTERISTIC_LIFE_EXCHANGE_OPERATION:
            return CHARACTERISTIC_LIFE_EXCHANGE_RULES
        if self.operation == DETAIN_OPERATION:
            return DETAIN_RULES
        if self.operation == DIRECT_PROHIBITION_OPERATION:
            return DIRECT_PROHIBITION_RULES
        if self.operation == PHASE_OUT_OPERATION:
            return PHASE_OUT_RULES
        if self.operation=='turn_face_up':
            from ..face_up_effect_model import FACE_UP_EFFECT_RULES
            return FACE_UP_EFFECT_RULES
        if self.operation == SADDLE_OPERATION:
            return ("400.7", "514.2", "702.171b")
        if self.operation == CAST_CASCADE_OPERATION:
            return CAST_CASCADE_RULES
        if self.operation == NEXT_SPELL_PAYMENT_OPERATION:
            return ("514.2", "601.2i", "611.2a", "702.51a", "702.126a")
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
        if operation == "end_turn":
            from ..rules.extra_turn_shapes import is_versioned_end_turn, validate_versioned_end_turn

            if is_versioned_end_turn(effect):
                try:
                    validate_versioned_end_turn(effect)
                except ValueError as exc:
                    raise SemanticNodeError(str(exc)) from exc
                if context.source is None or not context.source.stack_ref:
                    raise SemanticNodeError("Ending the turn requires the current resolving stack object")
        if operation == 'recover_source_result':
            from ..rules.recover import validate_recover_result
            try:validate_recover_result(effect,resolved=True)
            except (ValueError,TypeError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
            if (effect['player'] != context.actor or context.source is None
                    or context.source.logical_object_id != effect['identity']):
                raise SemanticNodeError('Recover result changed its original source or controller')
        if operation == 'reanimate' and ('attachment_return' in effect or effect.get('schema_version') == 7):
            from ..attachment_source_return_model import validate_attachment_source_return
            try:validate_attachment_source_return(effect)
            except (TypeError, ValueError, KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        if operation == 'reanimate' and effect.get('schema_version') == 2:
            from ..event_card_return import event_battlefield_return_intent
            try:
                intent = event_battlefield_return_intent(effect, actor=context.actor, reason=context.default_reason)
                context.query.require_known_seat(intent.new_controller)
            except (ValueError, TypeError, KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(operation=operation, handler_id=self.handler_id, intents=(intent,))
        if operation == 'reanimate' and effect.get('schema_version') in REANIMATION_ENTRY_COUNTER_VERSIONS:
            from ..reanimation_entry_counters import reanimation_entry_counter_intent
            try:
                intent=reanimation_entry_counter_intent(effect,actor=context.actor,reason=context.default_reason)
                context.query.require_known_seat(intent.new_controller)
            except (ValueError,TypeError,KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(operation=operation,handler_id=self.handler_id,intents=(intent,))
        if operation == 'resolve_fixed_object_action' and effect.get('schema_version') == 3:
            from ..rules.reanimation_followup import linked_departure_intent

            if context.source is None:
                raise SemanticNodeError('Delayed departure requires its stack source')
            try:
                intent = linked_departure_intent(effect, actor=context.actor, reason=context.default_reason)
            except (ValueError, TypeError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            return IntentPlan(operation=operation, handler_id=self.handler_id, intents=(intent,))
        reason = str(effect.get("reason") or context.default_reason)
        if not reason:
            raise SemanticNodeError("A domain effect requires a reason")
        if "_runtime_source" in effect or RUNTIME_SOURCE_CONTINUITY_FIELD in effect:
            raise SemanticNodeError(
                "Semantic programs cannot supply authoritative runtime source context"
            )
        if operation in {CONTROLLER_PRIVATE_TOP_EXILE_OPERATION, EACH_OPPONENT_TOP_EXILE_OPERATION,
                         OPPONENT_LIBRARY_EXILE_PLAY_OPERATION}:
            _validate_library_exile_domain_effect(operation, effect, context)
        if operation == SADDLE_OPERATION:
            ref = effect.get("card")
            if set(effect) - {"op", "card", "reason"} or type(ref) is not str or not ref:
                raise SemanticNodeError("Saddle requires a closed bound source")
        if 'event_player_set_version' in effect:
            from ..rules.event_player_modifiers import validate_bound_event_player_modifier
            try:validate_bound_event_player_modifier(effect)
            except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        runtime_effect = dict(effect)
        if operation in {'schedule_fixed_object_action','resolve_fixed_object_action'}:
            from ..delayed_object_model import validate_delayed_object_instruction
            try:validate_delayed_object_instruction({k:v for k,v in effect.items() if k!='reason'})
            except (TypeError,ValueError,KeyError) as exc:raise SemanticNodeError(str(exc)) from exc
        if operation == PHASE_OUT_OPERATION:
            try:
                subject = validate_phase_out_effect(effect, runtime=True)
                if subject in {"source", SOURCE_CONTROL_PHASE_OUT_SUBJECT} and context.source is None:
                    raise ValueError("Source phase out requires its typed source frame")
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
        if operation=='turn_face_up':
            from ..face_up_effect_model import (
                SOURCE_FACE_DOWN_SCHEMA_VERSION, validate_face_up_instruction, validate_source_face_down_instruction,
            )
            try:
                if effect.get("schema_version") == SOURCE_FACE_DOWN_SCHEMA_VERSION:
                    validate_source_face_down_instruction(effect)
                    if (effect["card"] is not None and context.source is not None
                            and context.source.card_ref != effect["card"]):
                        raise ValueError("Turning face down requires the resolving source")
                else:
                    validate_face_up_instruction(effect,allow_runtime_source=True)
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
        if operation == 'goad' and 'schema_version' in effect:
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
                _validate_linked_exile_return_instruction(effect, actor=context.actor)
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
        if operation == 'set_day_night':
            from ..day_night_model import validate_day_night_designation
            try:validate_day_night_designation(effect)
            except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        if operation == 'grant_next_spell_cost_reduction':
            from ..next_spell_cost_model import validate_next_spell_cost_instruction
            try:validate_next_spell_cost_instruction(effect,resolved=True)
            except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        if context.source is not None:
            runtime_effect["_runtime_source"] = context.source.to_dict()
            if SOURCE_TAP_EFFECT_FIELD in effect or uses_source_control_phase_out(effect):
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


def effect_descriptor_capability_dependencies(effect, registered):
    """Apply an exact immediate-object variant's narrower registered authority."""
    from ..rad_counter_model import RAD_CAPABILITY, versioned_rad_player_counter_effect

    if versioned_rad_player_counter_effect(effect):
        return (*registered["capability_dependencies"], RAD_CAPABILITY)
    if registered["operation"] == "exile_public_graveyard_card_result":
        from ..rules.graveyard_card_results import graveyard_card_counter_life_payload_dependencies

        return graveyard_card_counter_life_payload_dependencies(effect)
    if registered["operation"] == CONTROLLER_PRIVATE_TOP_EXILE_OPERATION:
        ControllerPrivateTopExile.from_instruction(effect)
    if registered["operation"] == EACH_OPPONENT_TOP_EXILE_OPERATION:
        from ..opponent_library_exile_model import DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY, DAMAGED_PLAYER_PRIVATE_TOP_VERSION

        validate_each_opponent_top_exile(effect)
        if effect["schema_version"] == DAMAGED_PLAYER_PRIVATE_TOP_VERSION:
            return (*registered["capability_dependencies"], DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY)
    if registered["operation"] == OPPONENT_LIBRARY_EXILE_PLAY_OPERATION:
        validate_opponent_library_exile_play(effect)
    if registered["operation"] == "grant_target_graveyard_escape":
        from ..rules.target_graveyard_escape import validate_target_graveyard_escape_effect

        validate_target_graveyard_escape_effect(effect)

    if registered["operation"] == "protection_from_everything_until_next_turn":
        from ..rules.next_personal_turn_protection import next_personal_turn_protection_dependencies

        required = next_personal_turn_protection_dependencies(effect)
        if required:
            return (*registered["capability_dependencies"], *required)
    if registered["operation"] == PHASE_OUT_OPERATION and (
        effect.get("schema_version") == 5 or effect.get("subject") == "controlled_permanent_set"
    ):
        from ..phasing_model import CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY

        validate_phase_out_effect(effect)
        return (*registered["capability_dependencies"], CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY)
    if registered["operation"] == "control_next_turn":
        from ..rules.extra_turn_shapes import is_versioned_next_turn_control, validate_versioned_next_turn_control

        if is_versioned_next_turn_control(effect):
            return (*registered["capability_dependencies"], *validate_versioned_next_turn_control(effect))
    if registered["operation"] == "end_turn":
        from ..rules.extra_turn_shapes import is_versioned_end_turn, validate_versioned_end_turn

        if is_versioned_end_turn(effect):
            return (*registered["capability_dependencies"], *validate_versioned_end_turn(effect))
    if registered["operation"] == "resolve_fixed_object_action":
        from ..delayed_object_model import FORCED_BLOCK_STATUS_CAPABILITIES, is_forced_block_status_effect

        if is_forced_block_status_effect(effect):
            return FORCED_BLOCK_STATUS_CAPABILITIES
    if registered["operation"] in {"schedule_fixed_object_action", "resolve_fixed_object_action"}:
        from ..delayed_object_model import TARGET_DEATH_RETURN_CAPABILITY, is_target_death_return_effect

        if is_target_death_return_effect(effect):
            return (*registered["capability_dependencies"], TARGET_DEATH_RETURN_CAPABILITY)
    if registered["operation"] == "copy_until_end_of_turn":
        from ..rules.color_retaining_copy import RETAINED_COLOR_COPY_CAPABILITY, is_retained_color_upkeep_copy
        from ..rules.persistent_self_copy_trigger import (
            PERSISTENT_SELF_COPY_TRIGGER_LEAF, is_persistent_self_copy_trigger, validate_self_copy_upkeep_instruction,
        )

        if is_persistent_self_copy_trigger(effect):
            validate_self_copy_upkeep_instruction(effect)
            return PERSISTENT_SELF_COPY_TRIGGER_LEAF
        if is_retained_color_upkeep_copy(effect):
            return (RETAINED_COLOR_COPY_CAPABILITY, "target.revalidate_resolution")
        from ..rules.linked_exile_copy import LINKED_EXILE_COPY_DEPENDENCIES, is_linked_exile_copy_effect

        if is_linked_exile_copy_effect(effect):
            return LINKED_EXILE_COPY_DEPENDENCIES
        from ..rules.announced_artifact_copy import (
            ANNOUNCED_ARTIFACT_COPY_DEPENDENCIES, is_announced_artifact_copy_effect,
        )
        from ..rules.announced_graveyard_copy import (
            ANNOUNCED_GRAVEYARD_COPY_DEPENDENCIES, is_announced_graveyard_copy_effect,
        )

        if is_announced_artifact_copy_effect(effect):
            return ANNOUNCED_ARTIFACT_COPY_DEPENDENCIES

        if is_announced_graveyard_copy_effect(effect):
            return ANNOUNCED_GRAVEYARD_COPY_DEPENDENCIES
        from ..rules.graveyard_exile_copy import (
            GRAVEYARD_EXILE_COPY_DEPENDENCIES, is_graveyard_exile_copy_effect,
        )

        if is_graveyard_exile_copy_effect(effect):
            return GRAVEYARD_EXILE_COPY_DEPENDENCIES
        from ..rules.persistent_public_copy import (
            CONTROLLED_ARTIFACT_COPY_DEPENDENCIES, PERSISTENT_PUBLIC_COPY_DEPENDENCIES,
            is_controlled_artifact_copy_effect, is_persistent_public_copy_effect,
        )

        if is_controlled_artifact_copy_effect(effect):
            return CONTROLLED_ARTIFACT_COPY_DEPENDENCIES
        if is_persistent_public_copy_effect(effect):
            return PERSISTENT_PUBLIC_COPY_DEPENDENCIES
    if registered["operation"] == "set_life_total":
        from ..life_total_setting_model import (
            CONTROLLED_TOUGHNESS_LIFE_CAPABILITY, CONTROLLED_TOUGHNESS_LIFE_KIND,
        )

        value = effect.get("value")
        if isinstance(value, Mapping) and value.get("kind") == CONTROLLED_TOUGHNESS_LIFE_KIND:
            return ("life.change.set_total", CONTROLLED_TOUGHNESS_LIFE_CAPABILITY)
    return registered["capability_dependencies"]


def _domain_capabilities(operation):
    if operation == CONTROLLER_PRIVATE_TOP_EXILE_OPERATION:
        return (CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY,)
    if operation == EACH_OPPONENT_TOP_EXILE_OPERATION:
        return (EACH_OPPONENT_TOP_EXILE_CAPABILITY,)
    if operation == OPPONENT_LIBRARY_EXILE_PLAY_OPERATION:
        return (OPPONENT_LIBRARY_EXILE_PLAY_CAPABILITY,)
    from ..target_source import TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY, TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION

    if operation == TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION:
        return (TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY,)
    if operation=='recover_source_result':
        from ..recover_model import RECOVER_RESULT_CAPABILITY
        return (RECOVER_RESULT_CAPABILITY,)
    if operation == "grant_target_graveyard_escape":
        from ..temporary_graveyard_escape import TEMPORARY_ESCAPE_CAPABILITY

        return (TEMPORARY_ESCAPE_CAPABILITY,)
    if operation=='grant_next_spell_cost_reduction':
        from ..next_spell_cost_model import CAPABILITY
        return (CAPABILITY,)
    if operation=='set_day_night':
        from ..day_night_model import DAY_NIGHT_DESIGNATION_CAPABILITY
        return (DAY_NIGHT_DESIGNATION_CAPABILITY,)
    if operation == DETAIN_OPERATION:
        return (DETAIN_CAPABILITY,)
    if operation == DIRECT_PROHIBITION_OPERATION:
        return (DIRECT_PROHIBITION_CAPABILITY,)
    if operation in {'schedule_fixed_object_action','resolve_fixed_object_action'}:
        from ..delayed_object_model import DELAYED_OBJECT_CAPABILITY
        return (DELAYED_OBJECT_CAPABILITY,)
    if operation=='set_life_total':return ('life.change.set_total',)
    if operation == PLAYER_LIFE_EXCHANGE_OPERATION:
        return (PLAYER_LIFE_EXCHANGE_CAPABILITY,)
    if operation == CHARACTERISTIC_LIFE_EXCHANGE_OPERATION:
        return (CHARACTERISTIC_LIFE_EXCHANGE_CAPABILITY,)
    if operation == PHASE_OUT_OPERATION:
        return PHASE_OUT_CAPABILITY, PHASING_CAPABILITY
    if operation=='turn_face_up':
        from ..face_up_effect_model import FACE_UP_EFFECT_CAPABILITY
        return (FACE_UP_EFFECT_CAPABILITY,)
    if operation==BLOCKING_REQUIREMENT_OPERATION:return (BLOCKING_REQUIREMENT_CAPABILITY,)
    if operation == SADDLE_OPERATION: return (SADDLE_CAPABILITY,)
    if operation == NEXT_SPELL_PAYMENT_OPERATION: return (NEXT_SPELL_PAYMENT_CAPABILITY,)
    if operation == CAST_CASCADE_OPERATION:
        return (CAST_CASCADE_CAPABILITY, "trigger.keyword.cascade")
    if operation in {LINKED_EXILE_RETURN_OPERATION,LINKED_RETURN_OPERATION}:return (LINKED_EXILE_RETURN_CAPABILITY,)
    return ()


DOMAIN_EFFECT_HANDLERS = tuple(
    DomainEffectHandler(operation=operation, contract=contract, capability_dependencies=_domain_capabilities(operation))
    for operation, contract in effect_operation_contracts()
    if operation not in {"bounce", "destroy", "mill", "create_compiled_emblem"}
)
