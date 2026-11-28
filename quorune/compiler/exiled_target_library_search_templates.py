from __future__ import annotations

from . import regex_working_set as re
from ..exiled_target_library_search import (
    EXILED_TARGET_SEARCH_MECHANIC,
    exiled_target_search_effects,
    owned_creature_search_target,
)


def equipped_exiled_target_search_result(binding, body: str):
    """Lower the closed result under its equipped combat damage trigger."""

    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("damage.dealt", "equipped_creature_combat_damage_a_player", "battlefield")
        or body != binding.body
    ):
        return None
    if not re.fullmatch(
        r"Exile up to one target creature you own, then search your library for a basic land card\. "
        r"Put both cards onto the battlefield under your control, then shuffle\.",
        " ".join(body.split()),
        re.IGNORECASE,
    ):
        return None
    return (
        "exiled-target-basic-land-search-v1",
        exiled_target_search_effects(),
        owned_creature_search_target(),
        (EXILED_TARGET_SEARCH_MECHANIC,),
    )
