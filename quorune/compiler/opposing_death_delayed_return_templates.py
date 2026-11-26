from __future__ import annotations

"""Compile the exact opposing death and its delayed entry instruction."""

from ..replacement.immutable import FrozenMap
from ..rules.opposing_death_delayed_return import (
    OPPOSING_DEATH_DELAYED_RETURN_MECHANIC,
    opposing_creature_death_condition,
    opposing_death_delayed_return_effect,
)


_BODY = (
    "return it to the battlefield under your control with an additional +1/+1 counter on it "
    "at the beginning of the next end step. "
    "that creature is a black zombie in addition to its other colors and types."
)


def opposing_death_delayed_return_template(binding, body):
    if (
        binding.event.value != "creature.dies" or binding.active_zone != "battlefield"
        or binding.variant != "creature:opponent:including_source:any_object"
        or FrozenMap(binding.event_condition or {}) != FrozenMap(opposing_creature_death_condition())
        or body != binding.body or body.casefold() != _BODY
    ):
        return None
    return (
        "opposing-death-delayed-counter-characteristics-v21",
        (opposing_death_delayed_return_effect(),),
        None,
        (OPPOSING_DEATH_DELAYED_RETURN_MECHANIC,),
    )
