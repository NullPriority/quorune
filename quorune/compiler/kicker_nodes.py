from __future__ import annotations

from typing import Any

from ..kicker import (
    compile_fixed_kicked_entry,
    compile_fixed_mana_kicker,
    kicked_entry_handler_descriptor,
    kicker_cost_handler_descriptor,
    KICKED_ENTRY_CAPABILITY_ID,
    KICKER_CAPABILITY_ID,
    KICKER_MECHANIC_ID,
    KICKER_RUNTIME_EVENT,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capability_gate
from .ir_model import append_residual, OracleNode, OracleResidual, SourceSpan


def fixed_mana_kicker_keyword_node(
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
    """Lower one single fixed ordinary-mana Kicker ability."""

    if mechanics != (KICKER_MECHANIC_ID,):
        return None
    from .nonmana_kicker import nonmana_kicker_keyword_node
    nonmana=nonmana_kicker_keyword_node(node_id=node_id,line=line,material_line=material_line,span=span,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if nonmana is not None:return nonmana
    spec = compile_fixed_mana_kicker(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    if spec is None:
        residual_id = append_residual(
            residuals,
            kind="keyword_grammar",
            text=line,
            span=span,
            reason="Kicker cost is outside the single fixed ordinary-mana grammar",
            blockers=(
                "multiple, and/or, variable, hybrid, Phyrexian, snow, and nonmana Kicker costs",
                "copied, granted, modified, and repeated Kicker instances",
            ),
        )
        return OracleNode(
            node_id=node_id,
            kind="keyword_ability",
            text=line,
            span=span,
            active_zone="all",
            event=KICKER_RUNTIME_EVENT,
            lowerable=False,
            exact=False,
            template_id="single-fixed-mana-kicker-residual-v1",
            mechanics=mechanics,
            residual_ids=(residual_id,),
        )
    gate = explicit_capability_gate(
        KICKER_CAPABILITY_ID,
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
                reason="fixed-mana Kicker lacks trusted capability closure",
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
        event=KICKER_RUNTIME_EVENT,
        lowerable=True,
        exact=not residual_ids,
        template_id="single-fixed-mana-kicker-v1",
        handlers=(kicker_cost_handler_descriptor(spec),),
        runtime_coverage=("optional_additional_mana_cost", "kicked_cast_fact"),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(gate.closure.reachable if gate.closure is not None else ()),
        capability_profile=(gate.closure.profile if gate.closure is not None else None),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def fixed_kicked_entry_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    **_unused: Any,
) -> OracleNode | None:
    """Lower the closed kicked counter-plus-keyword entry replacement."""

    if "was kicked" not in material_line.casefold():
        return None
    spec = compile_fixed_kicked_entry(material_line)
    if spec is None:
        return None
    gate = explicit_capability_gate(
        KICKED_ENTRY_CAPABILITY_ID,
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
                reason="fixed kicked entry lacks trusted capability closure",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="replacement_effect",
        text=line,
        span=span,
        active_zone="all",
        event="zone.change",
        lowerable=True,
        exact=not residual_ids,
        template_id="fixed-kicked-counter-keyword-entry-v1",
        handlers=(kicked_entry_handler_descriptor(spec),),
        runtime_coverage=(
            "kicked_cast_fact",
            "replacement_aware_entry_counters",
            "zone_object_entry_keyword",
        ),
        mechanics=(KICKER_MECHANIC_ID,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(gate.closure.reachable if gate.closure is not None else ()),
        capability_profile=(gate.closure.profile if gate.closure is not None else None),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


__all__ = ["fixed_kicked_entry_node", "fixed_mana_kicker_keyword_node"]
