from __future__ import annotations

"""Compile a literal optional collection without serializing its zone entries."""

import re

from ..private_hand_entry_model import (
    PRIVATE_HAND_COLLECTION_MECHANICS, PRIVATE_HAND_COLLECTION_OPERATION,
    PrivateHandCollectionSpec, private_hand_collection_query,
)


def private_hand_collection_template(text: str):
    match = re.fullmatch(
        r"(?:You may )?put (?P<count>up to two|any number of) "
        r"(?P<subject>creature|land|permanent|non-Saga permanent) cards from your hand "
        r"onto the battlefield(?P<tapped> tapped)?\.?",
        " ".join(text.strip().split()), re.IGNORECASE,
    )
    if match is None:
        return None
    spec = PrivateHandCollectionSpec(
        private_hand_collection_query(match["subject"]),
        maximum=2 if match["count"].casefold() == "up to two" else None,
        tapped=bool(match["tapped"]),
    )
    return (
        "private-hand-collection-v1",
        ({"op": PRIVATE_HAND_COLLECTION_OPERATION, "player": "$controller", "selection": spec.to_dict()},),
        None, PRIVATE_HAND_COLLECTION_MECHANICS,
    )
