"""Compile the paired graveyard exile and fixed counters before entry."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.linked_graveyard_entry import LINKED_GRAVEYARD_ENTRY_DEPENDENCIES, LINKED_GRAVEYARD_ENTRY_HANDLER
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_pattern
from . import regex_working_set as re
from .static_runtime_nodes import runtime_handler_node


def linked_graveyard_entry_node(
    *, node_id, line, material_line, span, source_name, announced_x_available,
    capability_registry, capability_profile, residuals,
):
    if not announced_x_available or "exile up to" not in material_line.casefold():
        return None
    source = SourceReferenceSpec(source_name).regex_pattern
    reference = rf"(?:{source}|{source_self_permanent_pattern()})"
    if re.fullmatch(
        rf"As {reference} enters, exile up to X creature cards from your graveyard\. "
        r"It enters with three \+1/\+1 counters on it for each creature card exiled this way\.?",
        " ".join(material_line.split()), re.IGNORECASE,
    ) is None:
        return None
    descriptor = {"handler_id": LINKED_GRAVEYARD_ENTRY_HANDLER, "schema_version": 1, "event": "zone.change"}
    return runtime_handler_node(
        node_id=node_id, line=line, span=span, kind="replacement_effect", event="zone.change",
        active_zone="all", runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        compiled=("announced-x-linked-graveyard-entry-v1", descriptor, LINKED_GRAVEYARD_ENTRY_DEPENDENCIES),
        dependency_reason="Linked graveyard entry uses the announced mana value, actual exiles and canonical counter placement.",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )


def entry_copy_or_linked_exile_node(**fields):
    from .entry_copy_templates import entry_copy_node

    result = linked_graveyard_entry_node(**fields)
    return result if result is not None else entry_copy_node(**fields)
