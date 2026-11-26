from __future__ import annotations

"""One exact nontargeted result over a controller's committed discard."""

from dataclasses import replace

from ..replacement.immutable import thaw_value
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_pattern
from ..rules.discarded_card_exile import (
    DISCARD_EVENT, DISCARD_EXILE_MECHANIC, controller_discard_condition, discarded_card_exile_effect,
)
from . import regex_working_set as re


def discarded_card_exile_template(binding, body):
    if (
        binding.event.value != DISCARD_EVENT or binding.active_zone != "battlefield"
        or binding.variant != "controller_discards_card" or body != binding.body
        or thaw_value(binding.event_condition) != controller_discard_condition()
        or re.fullmatch(r"exile that card from your graveyard\.", body, re.IGNORECASE) is None
    ):
        return None
    return "discarded-card-graveyard-exile-v1", (discarded_card_exile_effect(),), None, (DISCARD_EXILE_MECHANIC,)


def restrict_linked_discard_exile(nodes, *, oracle_text, source_name, residuals, append_residual):
    """Keep linked-exile producers unavailable until they can make the original links."""
    if not any(DISCARD_EXILE_MECHANIC in node.mechanics for node in nodes):
        return nodes
    source = SourceReferenceSpec(source_name).regex_pattern
    labels = source_self_permanent_pattern()
    if re.search(rf"\bexiled (?:with|by) (?:{source}|{labels}|this card)\b", oracle_text, re.IGNORECASE) is None:
        return nodes

    def reject(node):
        if DISCARD_EXILE_MECHANIC not in node.mechanics:
            return node
        residual_id = append_residual(
            residuals, kind="trigger_linked_ability", text=node.text, span=node.span,
            reason="Discarded-card exile cannot provide the source's linked exile collection",
            blockers=("linked discarded-card exile",),
        )
        return replace(node, exact=False, lowerable=False, effects=(), residual_ids=(*node.residual_ids, residual_id))

    return [reject(node) for node in nodes]
