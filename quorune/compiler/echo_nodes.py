from __future__ import annotations

from ..echo import (
    DISCARD_ECHO_CAPABILITY,
    DISCARD_ECHO_TEMPLATE,
    ECHO_MECHANIC_ID,
    compile_discard_echo,
    compile_fixed_mana_echo,
    echo_upkeep_condition,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capability_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan


def fixed_mana_echo_node(
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
    """Lower one of the closed fixed Echo payments."""

    if mechanics != (ECHO_MECHANIC_ID,):
        return None
    spec = compile_fixed_mana_echo(material_line)
    discard = compile_discard_echo(material_line) if spec is None else None
    if spec is None and discard is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_echo_cost",
            text=line,
            span=span,
            reason="Echo cost is outside the closed ordinary mana and one-card discard grammar",
            blockers=(
                "alternative, snow, hybrid, Phyrexian, and variable costs",
                "other nonmana costs, modifiers, and compound keyword wording",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="triggered_ability",
            text=line,
            span=span,
            active_zone="battlefield",
            event="step.begin",
            lowerable=False,
            exact=False,
            template_id="fixed-mana-echo-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    gate = explicit_capability_gate(
        DISCARD_ECHO_CAPABILITY if discard is not None else "trigger.keyword.echo.fixed_mana",
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
                reason=("discard Echo lacks a trusted capability closure" if discard is not None
                        else "fixed-mana Echo lacks a trusted capability closure"),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="step.begin",
        event_condition=echo_upkeep_condition(),
        lowerable=True,
        exact=not residual_ids,
        template_id=DISCARD_ECHO_TEMPLATE if discard is not None else "fixed-mana-echo-v1",
        effects=(discard if discard is not None else spec.effect_descriptor(),),
        runtime_coverage=("intervening_condition",),
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


__all__ = ["fixed_mana_echo_node"]
