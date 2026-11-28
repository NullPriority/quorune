"""Private choice presentation for a hand, bottom and exile play partition."""

from dataclasses import replace

from ..replacement.immutable import FrozenMap
from ..rules.library_selection import (
    LibrarySelectionArrangement, PRIVATE_PARTITION_BOTTOM_DESTINATION, PRIVATE_PARTITION_EXILE_DESTINATION,
)
from .model import LibraryPartitionChoice, SemanticChoiceRequest


def private_hand_bottom_exile_request(refs, public_context):
    return SemanticChoiceRequest(
        prompt="Choose one card for your hand. The first remaining card goes to the bottom of your library. "
               "The second remaining card goes to exile and may be played this turn.",
        choice=LibraryPartitionChoice(
            field_name="cards", legal_refs=tuple(refs), visibility="actor_private", legacy_destination=None,
            partitions=FrozenMap({
                "hand": {"order": "selection_order", "label": "Hand", "minimum": 1, "maximum": 1},
                "remainder": {"order": "destination_order", "label": "Bottom first, then exile",
                              "minimum": len(refs) - 1, "maximum": len(refs) - 1},
            }),
        ),
        public_context=public_context,
    )


def private_hand_bottom_exile_intents(primary):
    arrangement = primary.arrangement
    identities = {identity.ref: identity for identity in arrangement.looked}
    destinations = (PRIVATE_PARTITION_BOTTOM_DESTINATION, PRIVATE_PARTITION_EXILE_DESTINATION)
    return (primary, *(
        replace(primary, arrangement=LibrarySelectionArrangement(
            looked=(identities[ref],), selected_refs=(ref,), remainder_refs=(),
            remainder_destination="library_bottom", remainder_order="chosen", selected_destination=destination,
        ))
        for ref, destination in zip(arrangement.remainder_refs, destinations)
    ))
