from __future__ import annotations

"""Source-spanned Umbra armor; gameplay trust requires its destruction owner."""

from ..ability_fragments import ability_fragment_to_dict
from ..umbra_armor_model import (
    UmbraArmorSpec, UMBRA_ARMOR_CAPABILITY, UMBRA_ARMOR_FRAGMENT_CAPABILITY, UMBRA_ARMOR_HANDLER,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def umbra_armor_keyword_node(
    *, node_id: str, line: str, material_line: str, span: SourceSpan,
    mechanics: tuple[str, ...], capability_registry: CapabilityRegistry | None,
    capability_profile: str, residuals: list[OracleResidual],
) -> OracleNode | None:
    if mechanics != ("umbra armor",) or material_line.strip().rstrip(".").casefold() != "umbra armor":
        return None
    gate = explicit_capabilities_gate(
        (UMBRA_ARMOR_CAPABILITY, UMBRA_ARMOR_FRAGMENT_CAPABILITY),
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Umbra armor requires closed coupled destruction and affected-controller replacement choices",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    return OracleNode(
        node_id=node_id, kind="static_ability", text=line, span=span,
        active_zone="battlefield", event="characteristics.evaluate", lowerable=True,
        exact=not residual_ids, template_id="umbra-armor-source-fragment-v1",
        handlers=({"handler_id": UMBRA_ARMOR_HANDLER, "schema_version": 1,
                   "event": "characteristics.evaluate", "fragment": ability_fragment_to_dict(UmbraArmorSpec())},),
        mechanics=mechanics, residual_ids=residual_ids,
        runtime_coverage=("umbra_armor_source_fragment",),
        capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure is not None else (),
        capability_profile=gate.closure.profile if gate.closure is not None else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure is not None else None,
    )
