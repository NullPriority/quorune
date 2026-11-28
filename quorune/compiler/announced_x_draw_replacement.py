from __future__ import annotations

from . import regex_working_set as re
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC
from ..rules.announced_x_draw_replacement import TEMPLATE, announced_x_draw_replacement_effect


_BODY = re.compile(
    r"Draw X cards\. If X is 10 or more, instead shuffle your graveyard into your library, draw X cards, "
    r"untap up to five lands, and you have no maximum hand size for the rest of the game\.", re.IGNORECASE,
)


def announced_x_draw_replacement_template(text):
    if _BODY.fullmatch(" ".join(text.split())) is None:
        return None
    return TEMPLATE, (announced_x_draw_replacement_effect(),), None, (RESOLUTION_CONDITION_MECHANIC,)
