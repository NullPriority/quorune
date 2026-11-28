from __future__ import annotations

"""Exact payment-effect shapes beside the canonical stack counter owner."""

from typing import Any, Iterable, Mapping, Sequence

from .stack_controller_payment_cost import (
    STACK_CONTROLLER_PAYMENT_CAPABILITY, STACK_CONTROLLER_PAYMENT_MECHANIC,
    STACK_CONTROLLER_DESTINATION_PAYMENT_VERSION, STACK_CONTROLLER_FOLLOWUP_PAYMENT_CAPABILITY,
    STACK_CONTROLLER_FOLLOWUP_PAYMENT_VERSION, STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION,
    TARGET_ANNOUNCEMENT_PAYMENT_VERSION, target_announcement_payment_cost,
    compiled_stack_controller_payment_cost, counter_payment_followup,
)
from ..zone_trigger_events import COUNTER_DESTINATION_FIELD
from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value


def _followup_capabilities(effect, payment):
    from .fixed_effect_payment_shapes import fixed_payment_result_capabilities

    payload = counter_payment_followup(effect)
    projected = thaw_value(FrozenMap(payload["effect"]))
    operation = projected["op"]
    payer = "$target.current_controller.0"
    schema = None
    amount_field = {"damage": "amount", "life": "delta"}.get(operation)
    if amount_field is not None:
        amount = CastXAmountSpec.from_dict(projected[amount_field])
        if not payment.uses_cast_x or amount.coefficient != 1:
            return ()
        projected[amount_field] = 1
    if operation in {"damage", "choose_cards_apnap"}:
        if projected.get("target") != payer:
            return ()
        projected["target"] = "$target.0"
        schema = {"zones": ["player"], "categories": ["player"], "count": 1}
        if operation == "choose_cards_apnap":
            if projected.get("players") != [payer]:
                return ()
            projected["players"] = ["$target.0"]
            schema["player_relation"] = "any"
    else:
        actor_field = "controller" if operation == "create_token" else "player"
        if projected.get(actor_field) != "$controller":
            return ()
    owned = fixed_payment_result_capabilities(
        effects=(projected,), target_schema=schema, mechanic_ids=payload["mechanic_ids"],
    )
    if not owned:
        return ()
    return (STACK_CONTROLLER_FOLLOWUP_PAYMENT_CAPABILITY, *owned,
            *((DECLARED_AMOUNT_CAPABILITY,) if amount_field is not None else ()))


def source_opponent_target_announcement_condition_is_closed(condition: Any) -> bool:
    return thaw_value(condition) == {"all": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": "stack_controller", "op": "ne", "value": "$source.controller"},
    ]}


def controlled_sliver_opponent_target_announcement_condition() -> dict[str, Any]:
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "types", "op": "contains_any", "value": ["creature"]},
        {"field": "subtypes", "op": "contains_any", "value": ["sliver"]},
        {"field": "stack_controller", "op": "ne", "value": "$source.controller"},
    ]}


def target_announcement_payment_condition_is_closed(condition: Any) -> bool:
    return (
        source_opponent_target_announcement_condition_is_closed(condition)
        or thaw_value(condition) == controlled_sliver_opponent_target_announcement_condition()
    )


def stack_controller_payment_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = set(mechanic_ids)
    if STACK_CONTROLLER_PAYMENT_MECHANIC not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    from .whole_hand_counter_payment import marks_whole_hand_counter, whole_hand_counter_dependencies
    if marks_whole_hand_counter(effect):
        try:
            return whole_hand_counter_dependencies(effect, target_schema)
        except ValueError:
            return ()
    if isinstance(effect, Mapping) and effect.get("schema_version") == TARGET_ANNOUNCEMENT_PAYMENT_VERSION:
        if target_schema is not None or "trigger-event-normalized-target-announcement" not in mechanics:
            return ()
        try:
            target_announcement_payment_cost(effect)
        except (TypeError, ValueError, KeyError):
            return ()
        from .target_announcements import TARGET_ANNOUNCEMENT_CAPABILITY

        return (STACK_CONTROLLER_PAYMENT_CAPABILITY, "stack.counter.effect", TARGET_ANNOUNCEMENT_CAPABILITY)
    try:
        payment = compiled_stack_controller_payment_cost(effect)
    except (TypeError, ValueError, KeyError):
        return ()
    if (payment.schema_version == STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION
            or effects[0]["schema_version"] == STACK_CONTROLLER_FOLLOWUP_PAYMENT_VERSION) and (
        target_schema is None or tuple(target_schema.get("zones", ())) != ("stack",)
        or tuple(target_schema.get("categories", ())) != ("spell",)
    ):
        return ()
    from .node_capability_shapes import targeted_counter_node_capabilities
    counter_effect = {"op": "counter_stack_target", "stack": "$target.0"}
    if COUNTER_DESTINATION_FIELD in effects[0]:
        counter_effect[COUNTER_DESTINATION_FIELD] = effects[0][COUNTER_DESTINATION_FIELD]
    counter = targeted_counter_node_capabilities(
        effects=(counter_effect,),
        target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not counter:
        return ()
    followup = ()
    if effects[0]["schema_version"] == STACK_CONTROLLER_FOLLOWUP_PAYMENT_VERSION:
        try:
            followup = _followup_capabilities(effects[0], payment)
        except (TypeError, ValueError, KeyError):
            return ()
        if not followup:
            return ()
    return tuple(dict.fromkeys((STACK_CONTROLLER_PAYMENT_CAPABILITY, *counter, *payment.capabilities, *followup)))


def is_closed_stack_controller_payment_program(program: Any) -> bool:
    dependencies = set(stack_controller_payment_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage,
    ))
    return bool(dependencies) and dependencies <= set(program.capability_dependencies)


def stack_controller_payment_scope_dependencies(program: Any) -> tuple[str, ...]:
    """Revalidate the independent target roles of a composed payment clause."""
    from .effect_shape_walkers import nested_effects
    from .whole_hand_counter_payment import whole_hand_counter_runtime_dependencies

    if required := whole_hand_counter_runtime_dependencies(program):
        return required
    if any(effect.get("op") == "counter_unless_pay"
            and effect.get("schema_version") == TARGET_ANNOUNCEMENT_PAYMENT_VERSION
            for effect in nested_effects(program.effects)):
        from .target_announcements import TARGET_ANNOUNCEMENT_EVENT

        if ((program.event, program.active_zone) != (TARGET_ANNOUNCEMENT_EVENT, "battlefield")
                or not target_announcement_payment_condition_is_closed(program.event_condition)):
            raise ValueError("Target announcement payment requires an exact opponent target occurrence scope")
        owned = stack_controller_payment_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if not owned:
            raise ValueError("Target announcement payment requires its complete sealed stack result")
        return owned
    versioned = any(effect.get("op") == "counter_unless_pay"
                    and effect.get("schema_version") in {
                        STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION, STACK_CONTROLLER_DESTINATION_PAYMENT_VERSION,
                        STACK_CONTROLLER_FOLLOWUP_PAYMENT_VERSION,
                    } for effect in program.effects)
    if STACK_CONTROLLER_PAYMENT_MECHANIC not in program.coverage and not versioned:
        return ()
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies

    composed = composed_spell_family_runtime_dependencies(
        program, capability=STACK_CONTROLLER_PAYMENT_CAPABILITY,
        mechanic=STACK_CONTROLLER_PAYMENT_MECHANIC,
    )
    if composed is not None or not versioned:
        return composed or ()
    owned = stack_controller_payment_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Versioned controller payment requires its independently closed cost and spell target")
    return owned
