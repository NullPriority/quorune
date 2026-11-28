"""Compile a sealed land-entry supplier followed by a controller draw."""

from . import regex_working_set as re
from .draw_templates import fixed_draw_effect_template
from .impulse_access_templates import IMPULSE_ACCESS_MECHANIC_ID
from ..land_entry_library_exile_model import land_entry_top_exile_effect
from ..replacement.immutable import FrozenMap
from ..rules.land_entry_library_exile import OPPONENT_LAND_ENTRY_CONDITION


def land_entry_top_exile_result(binding, body):
    if ((binding.event.value, binding.active_zone, binding.variant)
            != ("land.enter", "battlefield", "opponent_land")
            or FrozenMap(binding.event_condition) != OPPONENT_LAND_ENTRY_CONDITION):
        return None
    if not re.fullmatch(
        r"That player exiles the top two cards of their library and you draw two cards\.?",
        body.strip(), re.I,
    ):
        return None
    _, (draw,), _, draw_mechanics = fixed_draw_effect_template("Draw two cards.")
    return (
        "opponent-land-entry-top-exile-draw-v1",
        (land_entry_top_exile_effect(), draw),
        None,
        (IMPULSE_ACCESS_MECHANIC_ID, *draw_mechanics),
    )
