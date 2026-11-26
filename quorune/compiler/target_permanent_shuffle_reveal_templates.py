from __future__ import annotations

"""Compile the complete fixed target-owner library shuffle and reveal."""

from ..rules.library_result_capability_shapes import target_permanent_shuffle_reveal_schema
from ..target_controller_information import TARGET_OWNER_INFORMATION_MECHANIC, target_owner_reference
from ..target_permanent_shuffle_reveal_model import WARP_MECHANIC, WARP_OPERATION


_PRINTED_BODY = (
    "The owner of target permanent shuffles it into their library, then reveals the top card of their library. "
    "If it's a permanent card, they put it onto the battlefield."
)


def target_permanent_shuffle_reveal_template(text: str):
    if text.strip().casefold() != _PRINTED_BODY.casefold():
        return None
    shuffle = {"op": "shuffle_into_library", "card": "$target.0"}
    reveal = {"op": WARP_OPERATION, "schema_version": 1, "player": target_owner_reference(0)}
    return (
        "target-permanent-shuffle-reveal-v1",
        (shuffle, reveal),
        target_permanent_shuffle_reveal_schema(),
        (WARP_MECHANIC, TARGET_OWNER_INFORMATION_MECHANIC, "cr-115-targets"),
    )
