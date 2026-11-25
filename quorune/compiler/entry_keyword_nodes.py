"""Ordered compound entry keywords before ordinary keyword planning."""
from .ravenous_nodes import ravenous_keyword_nodes
from .devour_nodes import devour_keyword_nodes
from .amplify_nodes import amplify_keyword_nodes
from .impending_nodes import fixed_mana_impending_nodes


def _counter_transfer_entry_nodes(*, record, node_id, line, material_line, span,
                                  capability_registry, capability_profile, residuals):
    normalized = " ".join(material_line.split()).casefold()
    if not record.is_creature or normalized != (
        "as this creature enters, remove all counters from all permanents. "
        "this creature enters with a +1/+1 counter on it for each counter removed this way."
    ):
        return None
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..semantic_runtime.self_entry_counters import (
        ENTRY_COUNTER_TRANSFER_DEPENDENCIES, ENTRY_COUNTER_TRANSFER_HANDLER_ID,
    )
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode, append_residual

    gate = explicit_capabilities_gate(ENTRY_COUNTER_TRANSFER_DEPENDENCIES,
        capability_registry=capability_registry, capability_profile=capability_profile)
    blockers = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Entry counter transfer requires canonical removal, entry, and counter quantity owners",
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return (OracleNode(
        node_id=node_id, kind="static_ability", text=line, span=span, active_zone="all", event="zone.change",
        lowerable=True, exact=not blockers, template_id="entry-permanent-counter-transfer-v1",
        handlers=({"handler_id": ENTRY_COUNTER_TRANSFER_HANDLER_ID, "schema_version": 1, "event": "zone.change"},),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), residual_ids=blockers,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    ),)


def compound_entry_keyword_nodes(*,record,node_id,line,material_line,span,printed_card_types,
        capability_registry,capability_profile,residuals):
    values=dict(node_id=node_id,line=line,material_line=material_line,span=span,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if impending := fixed_mana_impending_nodes(card_types=printed_card_types, **values):
        return impending
    ravenous=ravenous_keyword_nodes(record=record,printed_card_types=printed_card_types,**values)
    if ravenous is not None:
        return ravenous
    if transfer := _counter_transfer_entry_nodes(record=record, **values):
        return transfer
    devour = devour_keyword_nodes(**values)
    return devour if devour is not None else amplify_keyword_nodes(**values)
