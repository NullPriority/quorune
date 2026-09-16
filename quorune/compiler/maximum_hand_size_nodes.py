from __future__ import annotations

"""Compile the closed no-maximum-hand-size static permission."""

from ..maximum_hand_size import (
    NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID,
    NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID,
    no_maximum_hand_size_handler_descriptor,
)
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
    if material_line.casefold().rstrip(".") != "you have no maximum hand size":
        return None
    return runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=(
            NO_MAXIMUM_HAND_SIZE_TEMPLATE_ID,
            no_maximum_hand_size_handler_descriptor(),
            NO_MAXIMUM_HAND_SIZE_CAPABILITY_ID,
        ),
        kind="static_ability",
        event="characteristics.evaluate",
        dependency_reason=(
            "cleanup hand-size permission requires its shared current-component "
            "query"
        ),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


__all__ = ["no_maximum_hand_size_node"]
