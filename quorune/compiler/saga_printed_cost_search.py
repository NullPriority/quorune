from __future__ import annotations

"""Closed lowering for searches by an exact printed mana-cost class."""

from collections.abc import Mapping
from typing import Any

from . import regex_working_set as re
from .library_search_templates import FIXED_LIBRARY_SEARCH_MECHANIC_ID
from ..library_search_model import (
    PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE,
)


_PRINTED_COST_ARTIFACT_SEARCH = re.compile(
    r"Search your library for an artifact card with mana cost "
    r"\{0\} or \{1\}, put it onto the battlefield, then shuffle\.",
    re.IGNORECASE,
)
PRINTED_COST_SEARCH_CAPABILITY = PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID


def printed_mana_cost_artifact_search_template(
    text: str,
) -> tuple[str, tuple[Mapping[str, Any], ...], None, tuple[str, ...]] | None:
    """Compile only the exact printed-cost artifact search instruction.

    The search count stays one because the source says “a card.” The ordinary
    hidden-zone quality rule makes failure to find optional when an exact
    predicate is present. Battlefield placement is public when the move
    happens, so this instruction does not reveal the card while it remains in
    the library.
    """

    normalized = " ".join(text.strip().split())
    if _PRINTED_COST_ARTIFACT_SEARCH.fullmatch(normalized) is None:
        return None
    effect: dict[str, Any] = {
        "op": "search",
        "zone": "library",
        "selector": {
            "types": ["artifact"],
            "predicate": PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE,
        },
        "count": {"minimum": 1, "maximum": 1},
        "destination": "battlefield",
        "shuffle_after": True,
    }
    return (
        "printed-mana-cost-artifact-search-v1",
        (effect,),
        None,
        (
            FIXED_LIBRARY_SEARCH_MECHANIC_ID,
            PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID,
        ),
    )


__all__ = [
    "PRINTED_COST_SEARCH_CAPABILITY",
    "printed_mana_cost_artifact_search_template",
]
