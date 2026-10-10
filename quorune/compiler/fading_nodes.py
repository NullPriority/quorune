from __future__ import annotations

"""The upkeep half of fixed Fading, sharing the self-counter action owner."""

from ..rules.capabilities import CapabilityRegistry
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


FADING_UPKEEP_CAPABILITY = "counter.lifecycle.fading"


def fading_upkeep_node(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    gate = explicit_capabilities_gate(
        (FADING_UPKEEP_CAPABILITY, "counter.placement.quantity_replacement"),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="Fading upkeep lacks a trusted counter-removal and sacrifice owner",
            blockers=gate.blockers,
        ),)
        if gate.blockers else ()
    )
    return OracleNode(
        node_id=f"{node_id}:lifecycle",
        kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event="step.begin",
        event_condition={"all": [
            {"field": "player", "op": "eq", "value": "$source.controller"},
            {"field": "step", "op": "eq", "value": "upkeep"},
        ]},
        lowerable=True, exact=not residual_ids,
        template_id="fading-fixed-upkeep-v1",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        effects=({"op": "fixed_self_counter_keyword_action", "action": "fading",
                  "amount": 1, "source": "$source"},),
        mechanics=("fading", "cr-122-counters"), residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure is not None else (),
        capability_profile=gate.closure.profile if gate.closure is not None else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure is not None else None,
    )
