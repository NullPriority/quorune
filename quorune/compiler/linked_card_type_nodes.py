from __future__ import annotations

"""Lower a same-face card-type entry choice with its exact static consumer."""

from dataclasses import replace
from . import regex_working_set as re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..entry_designations import ENTRY_DESIGNATION_CAPABILITY
from ..linked_card_type import (
    CAPABILITY, COST_TEMPLATE, ENTRY_HANDLER, ENTRY_TEMPLATE, PROTECTION_CAPABILITY,
    PROTECTION_ENTRY_HANDLER, PROTECTION_ENTRY_TEMPLATE, PROTECTION_TEMPLATE,
)
from ..rules.source_references import SourceReferenceSpec
from ..semantic_runtime.chosen_characteristics import linked_card_type_protection_descriptor
from ..semantic_runtime.chosen_type_cast_costs import linked_card_type_cost_descriptor
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_COST = "spells you cast of the chosen type cost {1} less to cast."
_PROTECTION = "you and creatures you control have protection from the chosen card type."


def linked_card_type_face_rows(rows, source_name):
    materials = {span: material.casefold() for _, material, span in rows}
    choices = {span: material for span, material in materials.items() if material.endswith((
        "enters, choose a card type other than creature or land.", "enters, choose a card type.",
    ))}
    source = rf"(?:{SourceReferenceSpec(source_name).regex_pattern}|this (?:artifact|creature|enchantment|permanent))"
    pattern = re.compile(rf"As {source} enters, choose a card type( other than creature or land)?\.", re.I)
    roles = {}
    for span, material in choices.items():
        match = pattern.fullmatch(material)
        if match:
            roles[span] = "entry" if match[1] else "protection_entry"
    has_choice = bool(roles)
    for span, material in materials.items():
        if material == _PROTECTION:
            roles[span] = "protection"
        elif material == _COST and has_choice:
            roles[span] = "cost"
    paired = len(roles) == 2 and set(roles.values()) in (
        {"entry", "cost"}, {"protection_entry", "protection"},
    )
    return roles if paired else dict.fromkeys(roles, "unlinked")

def linked_card_type_node(*, role, node_id, line, span, residuals, capability_registry, capability_profile):
    if role == "unlinked":
        residual = append_residual(
            residuals, kind="entry_designation", text=line, span=span,
            reason="The exact card-type choice and its static consumer require one same-face linked pair",
            blockers=("linked card-type entry ownership",),
        )
        return OracleNode(
            node_id=node_id, kind="entry_designation", text=line, span=span, active_zone="all",
            event="unresolved", exact=False, lowerable=False, residual_ids=(residual,),
        )
    if role == "entry":
        compiled = (ENTRY_TEMPLATE, {
            "handler_id": ENTRY_HANDLER, "schema_version": 1, "event": "zone.change",
        }, (ENTRY_DESIGNATION_CAPABILITY, CAPABILITY))
        kind, event, zone = "replacement_effect", "zone.change", "all"
    elif role == "cost":
        compiled = (COST_TEMPLATE, linked_card_type_cost_descriptor(), (CAPABILITY,))
        kind, event, zone = "static_ability", "cast.cost.modify", "battlefield"
    elif role == "protection_entry":
        compiled = (PROTECTION_ENTRY_TEMPLATE, {
            "handler_id": PROTECTION_ENTRY_HANDLER, "schema_version": 1, "event": "zone.change",
        }, (ENTRY_DESIGNATION_CAPABILITY, PROTECTION_CAPABILITY))
        kind, event, zone = "replacement_effect", "zone.change", "all"
    else:
        compiled = (PROTECTION_TEMPLATE, linked_card_type_protection_descriptor(), (PROTECTION_CAPABILITY,))
        kind, event, zone = "static_ability", "characteristics.evaluate", "battlefield"
    return runtime_handler_node(
        node_id=node_id, line=line, span=span, compiled=compiled, kind=kind,
        event=event, active_zone=zone, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="The linked card-type choice and its static consumer require their shared authority",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )


def add_linked_card_type_context_nodes(
    blocks, rows, *, card_name, face_id, capability_registry, capability_profile,
):
    roles = linked_card_type_face_rows(rows, card_name)
    if not roles:
        return blocks
    nodes = dict(blocks.nodes)
    for index, (line, _material, span) in enumerate(rows):
        if span not in roles or index in blocks.consumed_rows or index in nodes:
            continue
        residuals = []
        node = linked_card_type_node(
            role=roles[span], node_id=f"{face_id}:n{index + 1}", line=line, span=span,
            residuals=residuals, capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        nodes[index] = ((node,), tuple(residuals))
    return replace(blocks, nodes=nodes)
