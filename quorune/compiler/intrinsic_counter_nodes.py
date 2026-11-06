from __future__ import annotations

from ..card_programs.admission import CompleteCardProgramAdmission
from ..empty_spell_resolution_model import INTRINSIC_COUNTER_PROHIBITION_TEMPLATE
from ..rules.capabilities import CapabilityRegistry
from .counter_templates import is_intrinsically_uncounterable_spell
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def intrinsic_counter_prohibition_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower the closed intrinsic spell-counter prohibition grammar."""

    if not is_intrinsically_uncounterable_spell(material_line):
        return None
    gate = explicit_capability_gate(
        "stack.counter.prohibition.intrinsic",
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
                    "intrinsic counter prohibition lacks a trusted "
                    "capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="stack",
        event="continuous",
        lowerable=True,
        exact=not gate.blockers,
        template_id=INTRINSIC_COUNTER_PROHIBITION_TEMPLATE,
        mechanics=("counter",),
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


def intrinsic_empty_spell_admission(record, ir, node):
    """Certify an empty body from the complete source, including omitted nodes."""
    if (
        node.template_id != INTRINSIC_COUNTER_PROHIBITION_TEMPLATE
        or record.layout != "normal" or record.faces
        or not (record.is_instant or record.is_sorcery)
        or len(ir.faces) != 1 or ir.faces[0].nodes != (node,)
    ):
        return None
    admission = CompleteCardProgramAdmission.from_oracle_ir(ir)
    return admission.to_dict() if admission.admitted else None


__all__ = ["intrinsic_counter_prohibition_node", "intrinsic_empty_spell_admission"]
