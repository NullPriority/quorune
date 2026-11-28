from __future__ import annotations

"""Lower one complete grouped public Mill counter trigger and the shared Rad result."""

from ..rules.committed_nonland_mill import (
    MILL_EVENT, MILL_MECHANIC, MILL_VARIANT, committed_mill_counter_effect,
    committed_mill_counter_target_schema, committed_mill_trigger_condition,
)
from .fixed_public_event_trigger_bindings import _spec


_MILL_HEAD = "whenever one or more nonland cards are milled, "
_MILL_BODY = ("put a +1/+1 counter on each of up to x target creatures, "
              "where x is the number of nonland cards milled this way.")


def committed_nonland_mill_binding_spec(material_line):
    normalized = " ".join(material_line.split())
    if normalized.casefold() != _MILL_HEAD + _MILL_BODY:
        return None
    return _spec(MILL_EVENT, MILL_VARIANT, normalized[len(_MILL_HEAD):],
                 "fixed-committed-nonland-mill-counter-trigger-v1", MILL_MECHANIC,
                 condition=committed_mill_trigger_condition())


def committed_nonland_mill_counter_result(binding, body):
    if (binding.variant != MILL_VARIANT or binding.event.value != MILL_EVENT
            or body != binding.body or body.casefold() != _MILL_BODY):
        return None
    from ..rules.counter_target_set_shapes import QUANTIFIED_COUNTER_MECHANIC

    return ("committed-nonland-mill-counter-targets-v1", (committed_mill_counter_effect(),),
            committed_mill_counter_target_schema(),
            ("cr-122-counters", "cr-115-targets", QUANTIFIED_COUNTER_MECHANIC, MILL_MECHANIC))


def source_enters_attacks_rad_result(binding, body):
    if (binding.variant != "source_enters_or_attacks" or binding.active_zone != "battlefield"
            or body != binding.body or body.casefold() != "each player gets a rad counter."):
        return None
    from .counter_placement_templates import fixed_player_counter_placement_effect_template

    template = fixed_player_counter_placement_effect_template(body)
    return template.compiled() if template is not None else None
