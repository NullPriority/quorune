from __future__ import annotations

"""CardProgram nodes for fixed public alternative casting costs."""

from collections.abc import Sequence
from typing import Any

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..public_alternative_costs import (
    FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY,
    FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
    FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC,
    FixedPublicAlternativeCostKind,
    fixed_public_alternative_cost_handler_descriptor,
)
from ..rules.capabilities import CapabilityRegistry
from .fixed_public_alternative_costs import (
    compile_fixed_public_alternative_cost,
)
from .cast_lifecycle_nodes import reject_repeated_suspend_nodes
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan
from .static_runtime_nodes import runtime_handler_node


_KEYWORDS = frozenset(
    {"freerunning", "prowl", "spectacle", "surge"}
)


def _compiled(spec: Any) -> tuple[str, dict[str, Any], str]:
    return (
        f"fixed-public-alternative-cost-{spec.kind.value}-v1",
        fixed_public_alternative_cost_handler_descriptor(spec),
        FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY,
    )


def fixed_public_alternative_cost_static_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one nonkeyword source-spanned alternative-cost line."""

    spec = compile_fixed_public_alternative_cost(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None or spec.kind is not FixedPublicAlternativeCostKind.PLAIN:
        return None
    return runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=_compiled(spec),
        kind="static_ability",
        event=FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
        active_zone="all",
        runtime_coverage=(
            FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC,
            CURRENT_ABILITY_FRAGMENT_COVERAGE,
        ),
        dependency_reason=(
            "fixed public alternative cost lacks trusted casting closure"
        ),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


def fixed_public_alternative_cost_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    **_unused: Any,
) -> OracleNode | None:
    """Lower one fixed-mana Spectacle, Surge, Prowl, or Freerunning line."""

    if len(mechanics) != 1 or mechanics[0] not in _KEYWORDS:
        return None
    spec = compile_fixed_public_alternative_cost(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None or spec.kind.value != mechanics[0]:
        residual_id = append_residual(
            residuals,
            kind="keyword_grammar",
            text=line,
            span=span,
            reason="Alternative-cost keyword is outside the fixed public grammar",
            blockers=(
                "one fixed ordinary-mana Spectacle, Surge, Prowl, or Freerunning declaration",
                "variable, hybrid, Phyrexian, snow, modified, copied, granted, and repeated alternatives remain unsupported",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="keyword_ability",
            text=line,
            span=span,
            active_zone="all",
            event=FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
            lowerable=False,
            exact=False,
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    return runtime_handler_node(
        node_id=node_id,
        line=line,
        span=span,
        compiled=_compiled(spec),
        kind="keyword_ability",
        event=FIXED_PUBLIC_ALTERNATIVE_COST_EVENT,
        active_zone="all",
        runtime_coverage=(
            FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC,
            CURRENT_ABILITY_FRAGMENT_COVERAGE,
        ),
        dependency_reason=(
            "fixed public alternative-cost keyword lacks trusted casting closure"
        ),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


def reject_public_alternative_cost_composition(
    nodes: Sequence[OracleNode],
    residuals: list[OracleResidual],
) -> tuple[OracleNode, ...]:
    """Reject repeated or independently cost-bearing sibling declarations."""

    positions = [
        index
        for index, node in enumerate(nodes)
        if FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC
        in node.runtime_coverage
    ]
    if not positions:
        return tuple(nodes)
    incompatible = len(positions) != 1 or any(
        index not in positions
        and (
            (
                node.event == FIXED_PUBLIC_ALTERNATIVE_COST_EVENT
                and node.cost is not None
            )
            or any(
                value in {
                    "bestow",
                    "buyback",
                    "dash",
                    "escape",
                    "evoke",
                    "foretell",
                    "jump-start",
                    "kicker",
                    "madness",
                    "plot",
                    "rebound",
                    "retrace",
                    "suspend",
                    "warp",
                }
                for value in node.mechanics
            )
        )
        for index, node in enumerate(nodes)
    )
    if not incompatible:
        return tuple(nodes)
    result = list(nodes)
    for index in positions:
        node = result[index]
        residual_id = append_residual(
            residuals,
            kind="alternative_cost_composition",
            text=node.text,
            span=node.span,
            reason=(
                "fixed public alternative cost has an unsupported sibling cost"
            ),
            blockers=(
                "one fixed public alternative cost without another alternative, lifecycle, or additional cost",
            ),
        )
        result[index] = OracleNode(
            node_id=node.node_id,
            kind=node.kind,
            text=node.text,
            span=node.span,
            active_zone=node.active_zone,
            event=node.event,
            lowerable=False,
            exact=False,
            mechanics=node.mechanics,
            residual_ids=(residual_id,),
        )
    return tuple(result)


def reject_cast_cost_composition(
    nodes: Sequence[OracleNode],
    residuals: list[OracleResidual],
) -> tuple[OracleNode, ...]:
    """Apply the fixed alternative and repeated-Suspend composition guards."""

    return reject_repeated_suspend_nodes(
        reject_public_alternative_cost_composition(nodes, residuals),
        residuals,
    )


__all__ = [
    "fixed_public_alternative_cost_keyword_node",
    "fixed_public_alternative_cost_static_node",
    "reject_cast_cost_composition",
    "reject_public_alternative_cost_composition",
]
