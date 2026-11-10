from __future__ import annotations

"""Exact payment-effect shapes beside the canonical stack counter owner."""

from typing import Any, Iterable, Mapping, Sequence

from .stack_controller_payment_cost import (
    STACK_CONTROLLER_PAYMENT_CAPABILITY, STACK_CONTROLLER_PAYMENT_MECHANIC,
    STACK_CONTROLLER_DESTINATION_PAYMENT_VERSION, STACK_CONTROLLER_FOLLOWUP_PAYMENT_CAPABILITY,
    STACK_CONTROLLER_FOLLOWUP_PAYMENT_VERSION, STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION,
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


def stack_controller_payment_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = set(mechanic_ids)
    if STACK_CONTROLLER_PAYMENT_MECHANIC not in mechanics or len(effects) != 1:
        return ()
    try:
        payment = compiled_stack_controller_payment_cost(effects[0])
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
