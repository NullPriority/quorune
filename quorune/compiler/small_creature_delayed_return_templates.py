"""Lower an optional next-end-step return with the original source guard."""

from . import regex_working_set as re
from ..delayed_object_model import (
    DELAYED_OBJECT_MECHANIC, small_creature_owner_graveyard_condition, source_bound_creature_return_effect,
)
from ..replacement.immutable import FrozenMap
from ..rules.source_references import SourceReferenceSpec


_SOURCE_BOUND_RETURN = re.compile(
    r"you may return that card to the battlefield at the beginning of the next end step "
    r"if (?P<source>.+?) is still on the battlefield\.?",
    re.I,
)


def small_creature_delayed_return_template(binding, body, *, card_name):
    if (binding.event.value != "permanent.graveyard" or binding.active_zone != "battlefield"
            or body != binding.body or not card_name
            or FrozenMap(binding.event_condition or {}) != FrozenMap(small_creature_owner_graveyard_condition())):
        return None
    match = _SOURCE_BOUND_RETURN.fullmatch(body)
    if match is None or not (
        match["source"].casefold() in {"this creature", "this permanent"}
        or SourceReferenceSpec(card_name).matches(match["source"])
    ):
        return None
    return ("source-bound-small-creature-return-v1", (source_bound_creature_return_effect(),),
            None, (DELAYED_OBJECT_MECHANIC,))
