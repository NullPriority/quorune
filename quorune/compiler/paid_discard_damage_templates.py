"""Compile an exact draw followed by damage qualified by a mandatory discard."""

from . import regex_working_set as re
from ..replacement.immutable import FrozenMap
from ..rules.paid_discard_damage import (
    PAID_NONLAND_DAMAGE_FIELD, PAID_NONLAND_DAMAGE_MECHANIC, paid_fixed_single_discard, paid_nonland_damage_effects,
)


def paid_nonland_discard_damage_template(text, cost_schema, *, compile_fixed):
    match = re.fullmatch(
        r"(?P<draw>Draw [^.!?]+\.) If the discarded card wasn['’]t a land card, (?P<damage>[^.!?]+\.)",
        " ".join(text.split()), re.I,
    )
    if match is None or not paid_fixed_single_discard(cost_schema):
        return None
    draw_id, draws, draw_targets, draw_mechanics = compile_fixed(match["draw"])
    damage_id, damages, targets, damage_mechanics = compile_fixed(match["damage"])
    if (draw_id is None or damage_id is None or draw_targets is not None or targets is not None
            or len(draws) != 1 or len(damages) != 1):
        return None
    try:
        effects = paid_nonland_damage_effects(draws[0]["count"], damages[0]["amount"])
    except (TypeError, ValueError, KeyError):
        return None
    expected_damage = {key: value for key, value in effects[1].items() if key != PAID_NONLAND_DAMAGE_FIELD}
    if FrozenMap(draws[0]) != FrozenMap(effects[0]) or FrozenMap(damages[0]) != FrozenMap(expected_damage):
        return None
    return ("draw-and-paid-nonland-discard-damage-v1", effects, None,
            (*draw_mechanics, *damage_mechanics, PAID_NONLAND_DAMAGE_MECHANIC))
