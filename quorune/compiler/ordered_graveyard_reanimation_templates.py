from __future__ import annotations

"""Compile one ordered graveyard return with its complete linked rider."""


_ORDERED_RETURN = (
    "return the top creature card of your graveyard to the battlefield. "
    "that creature gains haste until end of turn. "
    "exile it at the beginning of the next end step."
)


def compile_ordered_graveyard_reanimation(text, *, fallback, **context):
    if text.strip().casefold() != _ORDERED_RETURN:
        return fallback(text, **context)
    from ..rules.ordered_graveyard_reanimation import (
        ORDERED_GRAVEYARD_REANIMATION_MECHANIC, ordered_graveyard_reanimation_effect,
    )

    return (
        "ordered-graveyard-reanimation-haste-and-departure-v20",
        (ordered_graveyard_reanimation_effect(),),
        None,
        (ORDERED_GRAVEYARD_REANIMATION_MECHANIC,),
    )
