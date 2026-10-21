from __future__ import annotations

"""Compile closed fixed and unbounded static maximum-hand-size rules."""

import re

from ..maximum_hand_size import (
    NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID,
    NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID,
    no_maximum_hand_size_handler_descriptor,
    HAND_SIZE_ADJUSTMENT_CAPABILITY_ID,HAND_SIZE_ADJUSTMENT_TEMPLATE_ID,
    FixedHandSizeAdjustmentSpec,fixed_hand_size_adjustment_descriptor,
)
from .fixed_numbers import fixed_number
from ..rules.capabilities import CapabilityRegistry
from .ir_model import OracleNode, OracleResidual, SourceSpan
from .static_runtime_nodes import runtime_handler_node


def no_maximum_hand_size_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    if material_line.casefold().rstrip(".") == "you have no maximum hand size":
        compiled=(NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID,no_maximum_hand_size_handler_descriptor(),NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID)
    else:
        match=re.fullmatch(r"(?P<who>Your|Each opponent's) maximum hand size is (?P<direction>increased|reduced) by (?P<amount>one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+)\.?",material_line,re.I)
        if match is None:return None
        amount=fixed_number(match['amount'])
        if amount<1:return None
        spec=FixedHandSizeAdjustmentSpec('controller' if match['who'].casefold()=='your' else 'opponents',amount if match['direction'].casefold()=='increased' else -amount)
        compiled=(HAND_SIZE_ADJUSTMENT_TEMPLATE_ID,fixed_hand_size_adjustment_descriptor(spec),HAND_SIZE_ADJUSTMENT_CAPABILITY_ID)
    return runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=compiled,
        kind="static_ability",
        event="characteristics.evaluate",
        dependency_reason=(
            "cleanup hand-size rule requires its shared current-component "
            "query"
        ),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


__all__ = ["no_maximum_hand_size_node"]
