from __future__ import annotations

from ..commander_pairing import (
    COMMANDER_PAIRING_COVERAGE,
    COMMANDER_PAIRING_EVENT,
    COMMANDER_PAIRING_TEMPLATE_ID,
    PAIRING_CAPABILITY_BY_KIND,
    PARTNER_WITH_SEARCH_CAPABILITY_ID,
    PARTNER_WITH_SEARCH_MECHANIC_ID,
    PARTNER_WITH_SEARCH_TEMPLATE_ID,
    CommanderPairingKind,
    pairing_kind_for_material_line,
    partner_with_spec_for_material_line,
)
from ..ability_fragments import (
    CURRENT_ABILITY_FRAGMENT_COVERAGE,
    PARTNER_WITH_FRAGMENT_HANDLER_ID,
    ability_fragment_to_dict,
)
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import dependency_gate, explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def commander_pairing_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    **_: object,
) -> OracleNode | None:
    """Lower the exact ordinary Commander pairing declarations."""

    kind = pairing_kind_for_material_line(material_line)
    if kind is None or mechanics != (kind.value,):
        return None
    gate = explicit_capability_gate(
        PAIRING_CAPABILITY_BY_KIND[kind],
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
                    "Commander pairing eligibility depends on a blocked "
                    "typed setup capability"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    partner = partner_with_spec_for_material_line(material_line)
    handlers = (
        (
            {
                "handler_id": PARTNER_WITH_FRAGMENT_HANDLER_ID,
                "schema_version": 1,
                "event": COMMANDER_PAIRING_EVENT,
                "fragment": ability_fragment_to_dict(partner),
            },
        )
        if kind is CommanderPairingKind.PARTNER_WITH and partner is not None
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="all",
        event=COMMANDER_PAIRING_EVENT,
        lowerable=True,
        exact=not residual_ids,
        template_id=COMMANDER_PAIRING_TEMPLATE_ID,
        mechanics=mechanics,
        handlers=handlers,
        residual_ids=residual_ids,
        runtime_coverage=(COMMANDER_PAIRING_COVERAGE,),
        capability_dependencies=gate.capabilities,
        capability_closure=(
            closure.reachable if closure is not None else ()
        ),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


def partner_with_keyword_nodes(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    **_: object,
) -> tuple[OracleNode, OracleNode] | None:
    """Lower Partner with into its setup declaration and entry search."""

    partner = partner_with_spec_for_material_line(material_line)
    if partner is None or mechanics != (CommanderPairingKind.PARTNER_WITH.value,):
        return None
    setup = commander_pairing_keyword_node(
        node_id=f"{node_id}:pairing",
        line=line,
        material_line=material_line,
        span=span,
        mechanics=mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    if setup is None:
        return None
    target_schema = {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": "any",
        "count": 1,
    }
    effects = (
        {
            "op": "search",
            "searching_player": "$target.0",
            "zone": "library",
            "selector": {"names": [partner.partner_name]},
            "count": {"minimum": 0, "maximum": 1},
            "destination": "hand",
            "optional": True,
            "shuffle_after": True,
        },
    )
    trigger_mechanics = (
        "cr-603-handling-triggered-abilities",
        "cr-115-targets",
        "trigger-event-normalized-zone-change",
        PARTNER_WITH_SEARCH_MECHANIC_ID,
    )
    gate = dependency_gate(
        mechanics=trigger_mechanics,
        effects=effects,
        target_schema=target_schema,
        trusted_mechanics=trusted_mechanics,
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
                    "Partner with entry search lacks trusted target, trigger, "
                    "or named-library-search capability closure"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    search = OracleNode(
        node_id=f"{node_id}:search",
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="permanent.enter.self",
        lowerable=True,
        exact=not residual_ids,
        template_id=PARTNER_WITH_SEARCH_TEMPLATE_ID,
        effects=effects,
        target_schema=target_schema,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=trigger_mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            closure.reachable if closure is not None else ()
        ),
        capability_profile=(closure.profile if closure is not None else None),
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )
    return setup, search


__all__ = [
    "PARTNER_WITH_SEARCH_CAPABILITY_ID",
    "PARTNER_WITH_SEARCH_MECHANIC_ID",
    "PARTNER_WITH_SEARCH_TEMPLATE_ID",
    "commander_pairing_keyword_node",
    "partner_with_keyword_nodes",
]
