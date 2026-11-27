"""Close the published Commander restriction on an outside-game keyword."""

from ..rules.commander_companion_roles import (
    COMPANION_ROLE_CAPABILITY, COMPANION_ROLE_EVENT, COMPANION_ROLE_PROFILES, COMPANION_ROLE_TEMPLATE,
    SINGLETON_COMPANION_CLAUSE, commander_companion_is_banned,
)
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, append_residual


def commander_companion_role_node(*, record, node_id, line, material_line, span,
                                  capability_registry, capability_profile, residuals, **_unused):
    if " ".join(material_line.split()).casefold() != SINGLETON_COMPANION_CLAUSE:
        return None
    if (capability_profile not in COMPANION_ROLE_PROFILES
            or record.legalities.get("commander") != "legal"
            or not commander_companion_is_banned(record.oracle_id)):
        residual = append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="Companion setup has no admitted role in this profile for this source",
            blockers=("exact Commander companion-role authority",),
        )
        return OracleNode(node_id=node_id, kind="keyword_ability", text=line, span=span,
                          active_zone="all", event=COMPANION_ROLE_EVENT, lowerable=False, exact=False,
                          mechanics=("companion",), residual_ids=(residual,))
    gate = explicit_capability_gate(
        COMPANION_ROLE_CAPABILITY, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = ()
    if gate.blockers or gate.closure is None:
        residual_ids = (append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="Companion requires the published Commander role restriction", blockers=gate.blockers,
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="keyword_ability", text=line, span=span, active_zone="all",
        event=COMPANION_ROLE_EVENT, lowerable=True, exact=not residual_ids, template_id=COMPANION_ROLE_TEMPLATE,
        mechanics=("companion",), residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
