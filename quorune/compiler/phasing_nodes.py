from __future__ import annotations

"""Source-spanned literal Phasing at the untap turn action."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..phasing_model import PHASING_CAPABILITY, PHASING_EVENT, PHASING_HANDLER_ID
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, append_residual


def printed_phasing_keyword_node(
    *, node_id, line, material_line, span, mechanics, capability_registry, capability_profile, residuals, **_unused,
):
    if mechanics != ("phasing",):
        return None
    ordinary = material_line.strip().casefold() in {"phasing", "phasing."}
    gate = explicit_capability_gate(
        PHASING_CAPABILITY, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    blockers = gate.blockers if ordinary else ("mechanic:phasing-unsupported-wording",)
    residual_ids = (append_residual(
        residuals, kind="dependency_contract" if ordinary else "keyword_grammar",
        text=line, span=span, reason="Phasing requires its live untap lifecycle owner", blockers=blockers,
    ),) if blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="keyword_ability", text=line, span=span,
        active_zone="battlefield", event=PHASING_EVENT, lowerable=ordinary,
        exact=ordinary and not residual_ids, template_id="printed-untap-phasing-v1" if ordinary else None,
        handlers=({"handler_id": PHASING_HANDLER_ID, "schema_version": 1, "event": PHASING_EVENT},) if ordinary else (),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE, "typed_untap_phasing") if ordinary else (),
        mechanics=mechanics, residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
