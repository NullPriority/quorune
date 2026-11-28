from __future__ import annotations

from typing import Any

from ..flashback import (
    compile_fixed_mana_flashback,
    compile_fixed_sacrifice_flashback,
    FixedSacrificeFlashbackSpec,
    SACRIFICE_FLASHBACK_CAPABILITY_ID,
    flashback_handler_descriptor,
    FLASHBACK_CAPABILITY_ID,
    FLASHBACK_MECHANIC_ID,
    FLASHBACK_RUNTIME_EVENT,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


FLASHBACK_TEMPLATE_ID = "ordinary-fixed-mana-flashback-v1"


def ordinary_fixed_mana_flashback_keyword_node(
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
    """Lower one ordinary fixed Flashback ability or fail closed."""

    if mechanics != (FLASHBACK_MECHANIC_ID,):
        return None
    spec = compile_fixed_mana_flashback(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        spec = compile_fixed_sacrifice_flashback(
            material_line=material_line, oracle_line=line, line_index=span.line - 1,
        )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="keyword_grammar",
            text=line,
            span=span,
            reason="Flashback cost is outside the fixed grammar",
            blockers=(
                "fixed ordinary-mana, fixed-mana-plus-life, or three-creature Flashback",
                "variable, hybrid, Phyrexian, snow, wider nonmana, and modified Flashback costs",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="keyword_ability",
            text=line,
            span=span,
            active_zone="all",
            event=FLASHBACK_RUNTIME_EVENT,
            lowerable=False,
            exact=False,
            template_id="ordinary-flashback-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    if isinstance(spec, FixedSacrificeFlashbackSpec):
        capabilities = (SACRIFICE_FLASHBACK_CAPABILITY_ID,)
        template_id = "fixed-creature-sacrifice-flashback-v1"
        cost_coverage = ("fixed_creature_sacrifice_alternate_cost",)
    else:
        capabilities = (FLASHBACK_CAPABILITY_ID,)
        template_id = FLASHBACK_TEMPLATE_ID
        cost_coverage = ("fixed_mana_alternate_cost",)
        if spec.life_payment is not None:
            capabilities += ("casting.additional_cost.fixed_life_payment",)
            cost_coverage += ("fixed_life_payment",)
    gate = explicit_capabilities_gate(
        capabilities,
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
                reason="fixed Flashback lacks trusted capability closure",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="all",
        event=FLASHBACK_RUNTIME_EVENT,
        lowerable=True,
        exact=not residual_ids,
        template_id=template_id,
        handlers=(flashback_handler_descriptor(spec),),
        runtime_coverage=(
            "owner_graveyard_cast_permission",
            *cost_coverage,
            "flashback_stack_leave_replacement",
        ),
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
    "FLASHBACK_TEMPLATE_ID",
    "ordinary_fixed_mana_flashback_keyword_node",
]
