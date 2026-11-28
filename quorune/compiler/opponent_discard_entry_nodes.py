"""Closed printed opponent-discard redirection for a creature's current hand object."""

from . import regex_working_set as re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..opponent_discard_entry_model import OpponentDiscardEntrySpec
from .static_runtime_nodes import runtime_handler_node


_SOURCE_TEXT = re.compile(
    r"If a spell or ability an opponent controls causes you to discard this card, "
    r"put it onto the battlefield(?P<counters> with two \+1/\+1 counters on it)? "
    r"instead of putting it into your graveyard\.", re.IGNORECASE,
)


def opponent_discard_entry_node(*, type_line, node_id, line, material_line, span,
                                capability_registry, capability_profile, residuals):
    from ..characteristic_evaluation import type_parts

    match = _SOURCE_TEXT.fullmatch(material_line)
    if match is None or "creature" not in type_parts(type_line)[0]:
        return None
    spec = OpponentDiscardEntrySpec(2 if match["counters"] else 0)
    return runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("opponent-discard-entry-v1", spec.to_descriptor(), spec.dependencies),
        kind="replacement_effect", event="zone.change", active_zone="all",
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason="Opponent-discard entry requires the current source replacement and ordinary discard and entry owners",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
