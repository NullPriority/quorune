from __future__ import annotations

"""Lower a same-face card-type entry choice and its fixed own-spell reducer."""

from dataclasses import replace
from . import regex_working_set as re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..entry_designations import ENTRY_DESIGNATION_CAPABILITY
from ..linked_card_type import CAPABILITY, COST_TEMPLATE, ENTRY_HANDLER, ENTRY_TEMPLATE
from ..rules.source_references import SourceReferenceSpec
from ..semantic_runtime.chosen_type_cast_costs import linked_card_type_cost_descriptor
from .ir_model import OracleNode, append_residual
from .static_runtime_nodes import runtime_handler_node


_COST = "Spells you cast of the chosen type cost {1} less to cast."


def linked_card_type_face_rows(rows, source_name):
    candidate_rows = [row for row in rows if row[1].casefold().endswith(
        "enters, choose a card type other than creature or land.",
    )]
    if not candidate_rows:
        return {}
    source = rf"(?:{SourceReferenceSpec(source_name).regex_pattern}|this (?:artifact|creature|enchantment|permanent))"
    choice = re.compile(rf"As {source} enters, choose a card type other than creature or land\.", re.I)
    choices = [span for _, material, span in candidate_rows if choice.fullmatch(material)]
    if not choices:
        return {}
    consumers = [span for _, material, span in rows if material.casefold() == _COST.casefold()]
    paired = len(choices) == len(consumers) == 1
    return {span: ("entry" if span in choices else "cost") if paired else "unlinked"
            for span in (*choices, *consumers)}


def linked_card_type_node(*, role, node_id, line, span, residuals, capability_registry, capability_profile):
    if role == "unlinked":
        residual = append_residual(
            residuals, kind="entry_designation", text=line, span=span,
            reason="The exact card-type choice and its spell discount require one same-face linked pair",
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
    else:
        compiled = (COST_TEMPLATE, linked_card_type_cost_descriptor(), (CAPABILITY,))
        kind, event, zone = "static_ability", "cast.cost.modify", "battlefield"
    return runtime_handler_node(
        node_id=node_id, line=line, span=span, compiled=compiled, kind=kind,
        event=event, active_zone=zone, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Linked card-type choice and own-spell reduction require their shared authority",
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
