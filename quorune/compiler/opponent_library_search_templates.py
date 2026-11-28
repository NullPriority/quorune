"""Bind a self-counter and draw to the normalized opponent search."""

from . import regex_working_set as re
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from ..rules.source_references import SourceReferenceSpec
from ..rules.opponent_library_search import (
    LIBRARY_SEARCHED_EVENT, OPPONENT_OWN_LIBRARY_SEARCH_CAPABILITY, OPPONENT_OWN_LIBRARY_SEARCH_CONDITION,
    opponent_library_search_counter_draw_effects,
)


_VARIANT = "opponent_own_library_search_counter_draw"
_TEMPLATE = "opponent-own-library-search-counter-draw-v1"
_TRIGGER = re.compile(r"Whenever an opponent searches their library, (?P<body>.+)", re.IGNORECASE)
_RESULT = re.compile(r"put a \+1/\+1 counter on (?P<source>.+?) and draw a card\.", re.IGNORECASE)


def opponent_library_search_binding_spec(text, *, card_name):
    trigger = _TRIGGER.fullmatch(" ".join(text.split()))
    if trigger is None:
        return None
    result = _RESULT.fullmatch(trigger["body"])
    if result is None or not SourceReferenceSpec(card_name).matches(result["source"]):
        return None
    return FixedPublicEventBindingSpec(
        event=LIBRARY_SEARCHED_EVENT, variant=_VARIANT, body=trigger["body"],
        template_id=_TEMPLATE, mechanic="trigger-event-normalized-public-action",
        condition=OPPONENT_OWN_LIBRARY_SEARCH_CONDITION,
        capabilities=(OPPONENT_OWN_LIBRARY_SEARCH_CAPABILITY, "counter.producer.fixed_effect",
                      "zone.draw.library_to_hand"),
    )


def library_observer_binding_spec(text, *, card_name):
    from .opponent_library_shuffle_templates import opponent_library_shuffle_binding_spec

    return (opponent_library_search_binding_spec(text, card_name=card_name)
            or opponent_library_shuffle_binding_spec(text))


def opponent_library_search_result(binding, body):
    if binding.event.value != LIBRARY_SEARCHED_EVENT or binding.variant != _VARIANT or body != binding.body:
        return None
    return _TEMPLATE, opponent_library_search_counter_draw_effects(), None, ()
