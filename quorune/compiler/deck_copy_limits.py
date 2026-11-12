from __future__ import annotations

"""Source-spanned compiler declarations for printed deck copy limits."""

import re

from ..ability_fragments import ability_fragment_to_dict
from ..carddb import CardRecord
from ..deck_copy_limits import (
    DECK_COPY_LIMIT_CAPABILITY, DECK_COPY_LIMIT_EVENT, DECK_COPY_LIMIT_HANDLER_ID,
    DECK_COPY_LIMIT_TEMPLATE, DeckCopyLimitSpec,
)
from ..rules.capabilities import CapabilityRegistry
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .ir_model import OracleNode, OracleResidual, SourceSpan
from .oracle_source_text import material_source_lines
from .static_runtime_nodes import runtime_handler_node


DECK_COPY_LIMIT_PREFIX = "a deck can have "
_COPY_LIMIT = re.compile(
    rf"A deck can have (?:any number of|up to (?P<maximum>{FIXED_COUNT_PATTERN})) "
    r"cards named (?P<name>[^.]+)\.?", re.IGNORECASE,
)


def printed_deck_copy_limit_node(
    *, record: CardRecord, node_id: str, line: str, material_line: str, span: SourceSpan,
    capability_registry: CapabilityRegistry | None, capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    match = _COPY_LIMIT.fullmatch(material_line)
    if record.faces or match is None or match["name"] != record.name:
        return None
    if sum(text.casefold().startswith(DECK_COPY_LIMIT_PREFIX) for _, text, _ in material_source_lines(record.oracle_text)) != 1:
        return None
    try:
        maximum = fixed_number(match["maximum"]) if match["maximum"] is not None else None
        spec = DeckCopyLimitSpec(record.name, maximum)
    except ValueError:
        return None
    descriptor = {
        "handler_id": DECK_COPY_LIMIT_HANDLER_ID,
        "schema_version": 1,
        "event": DECK_COPY_LIMIT_EVENT,
        "fragment": ability_fragment_to_dict(spec),
    }
    return runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=(DECK_COPY_LIMIT_TEMPLATE, descriptor, DECK_COPY_LIMIT_CAPABILITY),
        kind="static_ability", event=DECK_COPY_LIMIT_EVENT, active_zone="deck",
        dependency_reason="Printed copy limits require their pregame validation capability",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )


def compiled_deck_copy_limit(card: CardRecord) -> DeckCopyLimitSpec | None:
    """Read the current exact declaration for one canonical deck record."""

    from ..oracle_ir import compile_oracle_card
    from ..rules.capabilities import load_default_capability_registry
    from ..semantic_runtime.deck_copy_limits import DeckCopyLimitFragmentHandler

    ir = compile_oracle_card(card, capability_registry=load_default_capability_registry(),
        capability_profile="commander_review")
    nodes = tuple(node for face in ir.faces for node in face.nodes if node.event == DECK_COPY_LIMIT_EVENT)
    if len(nodes) != 1 or not nodes[0].exact:
        return None
    node = nodes[0]
    if node.template_id != DECK_COPY_LIMIT_TEMPLATE or node.active_zone != "deck" or len(node.handlers) != 1:
        return None
    spec = DeckCopyLimitFragmentHandler().validate(node.handlers[0])
    return spec if spec.card_name == card.name else None
