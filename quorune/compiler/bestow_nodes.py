from __future__ import annotations

from ..bestow import (
    BESTOW_CAPABILITY_ID,
    BESTOW_MECHANIC_ID,
    BESTOW_RUNTIME_EVENT,
    bestow_handler_descriptor,
    compile_fixed_mana_bestow,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


BESTOW_TEMPLATE_ID = "fixed-mana-bestow-alternate-cost-v1"


def face_has_fixed_bestow(material_rows):
    """Read a face-local, independently parsed Aura mode before routing grants."""
    return any(compile_fixed_mana_bestow(material_line=material,oracle_line=line,line_index=span.line-1) is not None
        for line,material,span in material_rows)


def fixed_mana_bestow_keyword_node(
    *, node_id: str, line: str, material_line: str, span: SourceSpan,
    mechanics: tuple[str, ...], capability_registry: CapabilityRegistry | None,
    capability_profile: str, residuals: list[OracleResidual], **_: object,
) -> OracleNode | None:
    """Lower one ordinary fixed-mana Bestow cost to a typed descriptor."""

    if mechanics != (BESTOW_MECHANIC_ID,):
        return None
    spec = compile_fixed_mana_bestow(
        material_line=material_line,
        oracle_line=line,
        line_index=span.line - 1,
    )
    ordinary = spec is not None
    gate = explicit_capability_gate(
        BESTOW_CAPABILITY_ID,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = gate.blockers if ordinary else ("fixed ordinary-mana Bestow",)
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract" if ordinary else "keyword_grammar",
                text=line,
                span=span,
                reason=(
                    "Bestow depends on a blocked typed casting capability"
                    if ordinary
                    else "Bestow cost is outside the fixed ordinary-mana grammar"
                ),
                blockers=blockers,
            ),
        )
        if blockers else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="static_ability", text=line, span=span,
        active_zone="all", event=BESTOW_RUNTIME_EVENT,
        lowerable=ordinary, exact=ordinary and not residual_ids,
        template_id=BESTOW_TEMPLATE_ID if ordinary else None,
        handlers=(bestow_handler_descriptor(spec),) if spec is not None else (),
        mechanics=mechanics, residual_ids=residual_ids,
        runtime_coverage=("fixed_mana_bestow_cast_option",) if ordinary else (),
        capability_dependencies=gate.capabilities,
        capability_closure=(closure.reachable if closure is not None else ()),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(closure.fingerprint if closure is not None else None),
    )


__all__ = ["BESTOW_CAPABILITY_ID", "BESTOW_MECHANIC_ID", "BESTOW_TEMPLATE_ID", "fixed_mana_bestow_keyword_node"]
