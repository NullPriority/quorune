"""Lower an exact optional discard in place of the attempted entry."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.entry_land_discard import REQUIREMENTS, descriptor
from .static_runtime_nodes import runtime_handler_node


_TEXT = ("If this artifact would enter, you may discard a land card instead. "
         "If you do, put this artifact onto the battlefield. If you don't, put it into its owner's graveyard.")


def entry_land_discard_node(*, node_id, line, material_line, span, card_types, capability_registry,
                            capability_profile, residuals):
    if "artifact" not in card_types or " ".join(material_line.replace("’", "'").split()).casefold() != _TEXT.casefold():
        return None
    return runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("intrinsic-entry-land-discard-v1", descriptor(), REQUIREMENTS),
        kind="replacement_effect", event="zone.change", active_zone="all",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Entry land discard requires its private hand choice and ordered destination replacement.",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
