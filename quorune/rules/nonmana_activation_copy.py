"""Closed occurrence, resolution payment and copy source for a nonmana activation."""

from collections.abc import Mapping

from .nonmana_activation_copy_model import (
    ACTIVATION_COPY_CONTEXT, ABILITY_ACTIVATION_COPY_CAPABILITY, ABILITY_ACTIVATION_COPY_EVENT,
    ActivationCopySource, COPY_SOURCE_VERSION, PAYMENT_VERSION, TRIGGER_COPY_SOURCE_VERSION,
)
from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects

OPERATION = "offer_optional_mana_payment"
PAYMENT = FixedEffectPaymentSpec("mana", requirements={"GENERIC": 2, "W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0})


def marks_activation_copy_payment(effect):
    return effect.get("op") == OPERATION and effect.get("schema_version") == PAYMENT_VERSION


def nonmana_activation_condition():
    return {"field": "player", "op": "eq", "value": "$source.controller"}


def nonmana_activation_copy_payment(*, actor="$controller", target="$context.stack",
                                    source="$context." + ACTIVATION_COPY_CONTEXT):
    return {"op": OPERATION, "schema_version": PAYMENT_VERSION, "player": actor,
            "payment": PAYMENT.to_dict(), "cost": dict(PAYMENT.requirements),
            "effects": [{"op": "copy_stack_item", "stack": target, ACTIVATION_COPY_CONTEXT: source}]}


def validate_nonmana_activation_payment(effect, *, actor="$controller", target="$context.stack",
                                       source="$context." + ACTIVATION_COPY_CONTEXT):
    expected = nonmana_activation_copy_payment(actor=actor, target=target, source=source)
    if (type(effect.get("schema_version")) is not int or not isinstance(effect.get("cost"), Mapping)
            or any(type(value) is not int for value in effect["cost"].values())
            or thaw_value(FrozenMap(effect)) != expected
            or FixedEffectPaymentSpec.from_dict(effect["payment"]) != PAYMENT):
        raise ValueError("Activation copy payment lost its original controller, mana price or stack ability")


def nonmana_activation_copy_node_dependencies(effects, target_schema):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        validate_nonmana_activation_payment(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return (*PAYMENT.capabilities, ABILITY_ACTIVATION_COPY_CAPABILITY)


def nonmana_activation_copy_runtime_dependencies(program, subscriptions):
    nested = subscriptions is not None and subscriptions.subscription_for(ABILITY_ACTIVATION_COPY_EVENT) is not None
    marked = any(marks_activation_copy_payment(effect) or ACTIVATION_COPY_CONTEXT in effect
                 for effect in nested_effects((program.effects, program.handlers)))
    if program.event != ABILITY_ACTIVATION_COPY_EVENT and not nested and not marked:
        return ()
    required = nonmana_activation_copy_node_dependencies(program.effects, program.target_schema)
    if (program.event != ABILITY_ACTIVATION_COPY_EVENT or program.active_zone != "battlefield"
            or thaw_value(program.event_condition) != nonmana_activation_condition()
            or program.handlers or program.cost_schema is not None or program.trigger_limit is not None or not required):
        raise ValueError("Paid activation copying requires the exact observed nonmana activation and ordinary payment")
    return (*required, "trigger.event.normalized_public_action", "trigger.effect.fixed_event", "trigger.placement.apnap")


def publish_nonmana_activation_copy_occurrence(host, item):
    from .event_subscriptions import event_has_capability_subscriber

    if event_has_capability_subscriber(host, ABILITY_ACTIVATION_COPY_EVENT, ABILITY_ACTIVATION_COPY_CAPABILITY):
        host._dispatch_semantic_event(
            ABILITY_ACTIVATION_COPY_EVENT,
            {"event_id": item.stack_id, "player": item.controller, "stack": item.ref},
            sources=host._semantic_event_sources(zones={"battlefield"}),
        )


def capture_activation_copy_source(host, item, *, departed=False, schema_version=COPY_SOURCE_VERSION):
    from .activation.resolution import is_builtin_activation_semantic

    program = host.semantics.get(item.semantic_key)
    builtin = is_builtin_activation_semantic(item.semantic_key)
    if schema_version == TRIGGER_COPY_SOURCE_VERSION:
        from .attack_trigger_copy import builtin_attack_source_identity

        builtin = builtin_attack_source_identity(item) is not None
    if not (builtin or program is not None and host.semantic_program_is_current_trusted(program)):
        raise ValueError("The original ability has no trusted resolution")
    raw = item.to_dict()
    for key in ("semantic_frame", "currently_resolving", "declared_scalar_amounts",
                "targets_revalidated", "target_groups_current"):
        raw["context"].pop(key, None)
    return ActivationCopySource(raw, host._stack_target_schema(item, program), departed=departed,
                                schema_version=schema_version)


def retained_nonmana_activation_context(host, program, event, context):
    if not any(marks_activation_copy_payment(effect) for effect in program.effects):
        return {}
    if event != ABILITY_ACTIVATION_COPY_EVENT:
        raise ValueError("An activation copy must retain its actual activation occurrence")
    original = next((item for item in host.state.stack if item.ref == context.get("stack")), None)
    if original is None or original.stack_id != context.get("event_id") or original.controller != context.get("player"):
        raise ValueError("An activation copy lost its committed stack object")
    return {ACTIVATION_COPY_CONTEXT: capture_activation_copy_source(host, original).to_dict()}
