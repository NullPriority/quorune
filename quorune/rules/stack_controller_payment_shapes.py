from __future__ import annotations

"""Exact payment-effect shapes beside the canonical stack counter owner."""

from typing import Any, Iterable, Mapping, Sequence

from .stack_controller_payment_cost import (
    STACK_CONTROLLER_PAYMENT_CAPABILITY, STACK_CONTROLLER_PAYMENT_MECHANIC,
    compiled_stack_controller_payment_cost,
)


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
    from .node_capability_shapes import targeted_counter_node_capabilities
    counter = targeted_counter_node_capabilities(
        effects=({"op": "counter_stack_target", "stack": "$target.0"},),
        target_schema=target_schema, mechanic_ids=mechanics,
    )
    return (STACK_CONTROLLER_PAYMENT_CAPABILITY, *counter, *payment.capabilities) if counter else ()


def is_closed_stack_controller_payment_program(program: Any) -> bool:
    dependencies = set(stack_controller_payment_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage,
    ))
    return bool(dependencies) and dependencies <= set(program.capability_dependencies)
