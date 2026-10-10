from __future__ import annotations

"""Compile the fixed entry-counter component of compound keywords."""

import re

from ..fixed_keyword_entry_counters import (
    FIXED_KEYWORD_ENTRY_CAPABILITY,
    FIXED_KEYWORD_ENTRY_MECHANICS,
    FixedKeywordEntryCounterSpec,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capabilities_gate
from .fading_nodes import fading_upkeep_node
from .ir_model import (
    OracleNode,
    OracleResidual,
    SourceSpan,
    append_residual,
)


def fixed_keyword_entry_nodes(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> tuple[OracleNode, ...]:
    """Lower one fixed entry component and retain its later lifecycle."""

    if len(mechanics) != 1 or mechanics[0] not in FIXED_KEYWORD_ENTRY_MECHANICS:
        return ()
    mechanic = mechanics[0]
    match = re.fullmatch(
        rf"{re.escape(mechanic)}\s+(?P<amount>[1-9]\d*)\.?",
        material_line.strip(),
        re.IGNORECASE,
    )
    if match is None:
        residual_id = append_residual(
            residuals,
            kind="keyword_grammar",
            text=line,
            span=span,
            reason=(
                f"{mechanic.title()} has no represented positive fixed "
                "entry-counter component"
            ),
            blockers=(
                f"mechanic:{mechanic}-nonfixed-entry",
                f"mechanic:{mechanic}-remaining-lifecycle",
            ),
        )
        return (
            OracleNode(
                node_id=node_id,
                kind="keyword_ability",
                text=line,
                span=span,
                active_zone="battlefield",
                event="unresolved",
                lowerable=False,
                exact=False,
                mechanics=(mechanic,),
                residual_ids=(residual_id,),
            ),
        )

    spec = FixedKeywordEntryCounterSpec(mechanic, int(match.group("amount")))
    gate = explicit_capabilities_gate(
        (FIXED_KEYWORD_ENTRY_CAPABILITY, "counter.placement.quantity_replacement"),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    dependency_residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    f"{mechanic.title()} entry counters depend on a blocked "
                    "typed capability"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    lifecycle_node = fading_upkeep_node(
        node_id=node_id, line=line, span=span,
        capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
    ) if mechanic == "fading" else None
    lifecycle_residual_id = append_residual(
        residuals,
        kind="keyword_lifecycle",
        text=line,
        span=span,
        reason=(
            f"{mechanic.title()} counter removal, movement, trigger, or "
            "sacrifice behavior remains outside this entry-counter component"
        ),
        blockers=(f"mechanic:{mechanic}-remaining-lifecycle",),
    ) if lifecycle_node is None else None
    return (
        OracleNode(
            node_id=f"{node_id}:entry",
            kind="static_ability",
            text=line,
            span=span,
            active_zone="all",
            event="zone.change",
            lowerable=True,
            exact=not gate.blockers,
            template_id=spec.template_id,
            handlers=(spec.handler_descriptor(),),
            runtime_coverage=("fixed_keyword_self_entry_counter",),
            mechanics=(mechanic,),
            residual_ids=dependency_residual_ids,
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
        ),
        lifecycle_node or OracleNode(
            node_id=f"{node_id}:lifecycle",
            kind="keyword_ability",
            text=line,
            span=span,
            active_zone="battlefield",
            event="unresolved",
            lowerable=False,
            exact=False,
            mechanics=(mechanic,),
            residual_ids=(lifecycle_residual_id,) if lifecycle_residual_id is not None else (),
        ),
    )


__all__ = ["fixed_keyword_entry_nodes"]
