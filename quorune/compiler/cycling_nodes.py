from __future__ import annotations

from typing import Any

from ..cycling_abilities import (
    CYCLING_MECHANIC_ID,
    compile_ordinary_cycling_ability,
    compile_typecycling_ability,
    ordinary_cycling_handler_descriptor,
    typecycling_handler_descriptor,
)
from ..rules.capabilities import CapabilityRegistry
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capability_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan


TYPECYCLING_CAPABILITY_ID = "activation.typecycling.hand"


def typecycling_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one fixed Typecycling keyword through the shared search owner."""

    if mechanics != (CYCLING_MECHANIC_ID,):
        return None
    spec = compile_typecycling_ability(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        return None
    gate = explicit_capability_gate(
        TYPECYCLING_CAPABILITY_ID,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "Typecycling activation lacks a trusted capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="activated_ability",
        text=line,
        span=span,
        active_zone="hand",
        event="activate",
        lowerable=True,
        exact=not residual_ids,
        template_id="fixed-typecycling-activation-v1",
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=(spec.search_effect(),),
        handlers=(typecycling_handler_descriptor(spec),),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def ordinary_cycling_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    if mechanics != (CYCLING_MECHANIC_ID,):
        return None
    spec = compile_ordinary_cycling_ability(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_cycling_cost",
            text=line,
            span=span,
            reason=(
                "Cycling cost is outside the closed fixed ordinary mana grammar"
            ),
            blockers=(
                "variable and hybrid Cycling costs",
                "nonmana Cycling costs",
                "unsupported Typecycling, modifiers, prohibitions, and "
                "Cycling triggers",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="activated_ability",
            text=line,
            span=span,
            active_zone="hand",
            event="activate",
            lowerable=False,
            exact=False,
            template_id="ordinary-cycling-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    gate = explicit_capability_gate(
        "activation.cycling.hand",
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "ordinary Cycling activation lacks a trusted capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="activated_ability",
        text=line,
        span=span,
        active_zone="hand",
        event="activate",
        lowerable=True,
        exact=not residual_ids,
        template_id="ordinary-cycling-activation-v1",
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=(
            {
                "op": "draw",
                "player": "$controller",
                "count": 1,
                "private": True,
            },
        ),
        handlers=(ordinary_cycling_handler_descriptor(spec),),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


__all__ = [
    "TYPECYCLING_CAPABILITY_ID",
    "ordinary_cycling_keyword_node",
    "typecycling_keyword_node",
]
