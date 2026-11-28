"""Ordered compound entry keywords before ordinary keyword planning."""
from .ravenous_nodes import ravenous_keyword_nodes
from .devour_nodes import devour_keyword_nodes
from .amplify_nodes import amplify_keyword_nodes
from .impending_nodes import fixed_mana_impending_nodes
from . import regex_working_set as re


_CHOSEN_TRANSFER = re.compile(
    r"as (?P<source>.+?) enters, remove all counters from any number of "
    r"(?P<first>artifacts|creatures|enchantments), (?P<second>artifacts|creatures|enchantments),? "
    r"and (?P<third>artifacts|creatures|enchantments)\. (?P<entrant>.+?) enters with x "
    r"\+1/\+1 counters on it, where x (?:is|equals) twice the number of counters removed this way\.?",
    re.IGNORECASE,
)


def _counter_transfer_entry_nodes(*, record, node_id, line, material_line, span,
                                  capability_registry, capability_profile, residuals):
    if not record.is_creature:
        return None
    normalized = " ".join(material_line.split())
    all_permanents = normalized.casefold() == (
        "as this creature enters, remove all counters from all permanents. "
        "this creature enters with a +1/+1 counter on it for each counter removed this way."
    )
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..rules.source_references import SourceReferenceSpec
    from ..semantic_runtime.self_entry_counters import (
        CHOSEN_ENTRY_COUNTER_TRANSFER_HANDLER_ID, ENTRY_COUNTER_TRANSFER_DEPENDENCIES, ENTRY_COUNTER_TRANSFER_HANDLER_ID,
    )
    from .dependency_gate import explicit_capabilities_gate
    from .ir_model import OracleNode, append_residual

    handler_id = ENTRY_COUNTER_TRANSFER_HANDLER_ID
    template_id = "entry-permanent-counter-transfer-v1"
    if not all_permanents:
        match = _CHOSEN_TRANSFER.fullmatch(normalized)
        if match is None or len({match[name].casefold() for name in ("first", "second", "third")}) != 3:
            return None
        source = SourceReferenceSpec(record.name)
        if any(match[name].casefold() != "this creature" and not source.matches(match[name])
               for name in ("source", "entrant")):
            return None
        handler_id = CHOSEN_ENTRY_COUNTER_TRANSFER_HANDLER_ID
        template_id = "chosen-public-entry-counter-transfer-v1"

    gate = explicit_capabilities_gate(ENTRY_COUNTER_TRANSFER_DEPENDENCIES,
        capability_registry=capability_registry, capability_profile=capability_profile)
    blockers = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Entry counter transfer requires canonical removal, entry, and counter quantity owners",
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return (OracleNode(
        node_id=node_id, kind="static_ability", text=line, span=span, active_zone="all", event="zone.change",
        lowerable=True, exact=not blockers, template_id=template_id,
        handlers=({"handler_id": handler_id, "schema_version": 1, "event": "zone.change"},),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), residual_ids=blockers,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    ),)


def compound_entry_keyword_nodes(*,record,node_id,line,material_line,span,printed_card_types,
        capability_registry,capability_profile,residuals):
    values=dict(node_id=node_id,line=line,material_line=material_line,span=span,
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    if "land" in printed_card_types and "Hideaway" in record.keywords:
        from .hideaway_nodes import hideaway_keyword_node

        if hideaway := hideaway_keyword_node(**values):
            return (hideaway,)
    if impending := fixed_mana_impending_nodes(card_types=printed_card_types, **values):
        return impending
    ravenous=ravenous_keyword_nodes(record=record,printed_card_types=printed_card_types,**values)
    if ravenous is not None:
        return ravenous
    if transfer := _counter_transfer_entry_nodes(record=record, **values):
        return transfer
    devour = devour_keyword_nodes(**values)
    return devour if devour is not None else amplify_keyword_nodes(**values)
