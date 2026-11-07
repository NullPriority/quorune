from __future__ import annotations

import re

from ..ability_fragment_primitives import CREW_POWER_BONUS_CAPABILITY, CREW_POWER_BONUS_HANDLER_ID, CrewPowerBonusSpec
from ..ability_fragments import ability_fragment_to_dict
from ..crew import (
    CREW_CAPABILITY_ID,
    CREW_MECHANIC_ID,
    compile_ordinary_crew_ability,
    ordinary_crew_handler_descriptor,
)
from ..rules.capabilities import CapabilityRegistry
from .activated_costs import activated_ability_cost
from .dependency_gate import explicit_capability_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan


def crew_power_bonus_handler(text: str):
    """Compile a complete fixed self-relative Crew power clause."""
    match = re.fullmatch(
        r"This creature crews Vehicles as though its power were ([1-9][0-9]*) greater\.?",
        text, re.IGNORECASE,
    )
    if match is None:
        return None
    spec = CrewPowerBonusSpec(int(match[1]))
    return "crew-power-bonus-v1", {
        "handler_id": CREW_POWER_BONUS_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "fragment": ability_fragment_to_dict(spec),
    }, CREW_POWER_BONUS_CAPABILITY


def ordinary_crew_keyword_node(
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
    """Lower one source-spanned printed ``Crew N`` activation."""

    if mechanics != (CREW_MECHANIC_ID,):
        return None
    spec = compile_ordinary_crew_ability(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_crew_cost",
            text=line,
            span=span,
            reason="Crew is outside the closed fixed nonnegative power grammar",
            blockers=(
                "variable or nonnumeric Crew thresholds",
                "alternative or additional Crew costs",
                "Crew permission and prohibition modifiers",
                "granted, copied, removed, or text-changing Crew",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="activated_ability",
            text=line,
            span=span,
            active_zone="battlefield",
            event="activate",
            lowerable=False,
            exact=False,
            template_id="ordinary-crew-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    gate = explicit_capability_gate(
        CREW_CAPABILITY_ID,
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
                reason="ordinary Crew lacks trusted capability closure",
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
        active_zone="battlefield",
        event="activate",
        lowerable=True,
        exact=not residual_ids,
        template_id="ordinary-crew-activation-v1",
        cost=activated_ability_cost(spec.to_activated_ability()),
        effects=(
            {
                "op": "add_types_until_end_of_turn",
                "card": "$source.zone_object",
                "types": ["Artifact", "Creature"],
            },
        ),
        handlers=(ordinary_crew_handler_descriptor(spec),),
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


__all__ = ["crew_power_bonus_handler", "ordinary_crew_keyword_node"]
