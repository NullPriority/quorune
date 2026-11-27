"""Lower the exact private look, optional exile and return after an opponent shuffle."""

from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from ..rules.opponent_library_shuffle import (
    LIBRARY_SHUFFLED_EVENT, OPPONENT_SHUFFLE_CAPABILITIES, OPPONENT_SHUFFLE_CONDITION,
    opponent_shuffle_top_effect,
)

_PREFIX = "whenever an opponent shuffles their library, "
_BODY = ("you may look at the top two cards of that library. you may exile one of those cards. "
         "then put the rest on top of that library in any order.")
_VARIANT = "opponent_library_shuffle_top_partition"
_TEMPLATE = "fixed-opponent-library-shuffle-top-partition-v1"


def opponent_library_shuffle_binding_spec(text):
    if " ".join(text.split()).casefold() != _PREFIX + _BODY:
        return None
    return FixedPublicEventBindingSpec(
        event=LIBRARY_SHUFFLED_EVENT, variant=_VARIANT, body=text.split(",", 1)[1].strip(),
        template_id=_TEMPLATE, mechanic="trigger-event-normalized-public-action",
        condition=OPPONENT_SHUFFLE_CONDITION, capabilities=OPPONENT_SHUFFLE_CAPABILITIES,
    )


def opponent_library_shuffle_result(binding, body):
    if (binding.event.value != LIBRARY_SHUFFLED_EVENT or binding.variant != _VARIANT
            or body != binding.body or " ".join(body.split()).casefold() != _BODY):
        return None
    return _TEMPLATE, (opponent_shuffle_top_effect(),), None, ()
