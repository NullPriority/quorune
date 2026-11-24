"""A fixed mana price and a variable colored reveal before source sacrifice."""

from dataclasses import replace

from ..colored_hand_reveal_model import VariableColoredRevealPrice

from . import regex_working_set as re


_COLORS = dict(zip(("white", "blue", "black", "red", "green"), "WUBRG", strict=True))
_PRICE = re.compile(
    r"\{(?P<mana>[1-9][0-9]*)\}, Reveal X (?P<color>white|blue|black|red|green) "
    r"cards from your hand, Sacrifice this creature",
    re.IGNORECASE,
)


def variable_colored_reveal_price(cost_text: str) -> VariableColoredRevealPrice | None:
    match = _PRICE.fullmatch(" ".join(cost_text.split()))
    if match is None:
        return None
    return VariableColoredRevealPrice(int(match["mana"]), _COLORS[match["color"].casefold()])


def specialize_variable_colored_reveal_price(ability):
    if len(ability.uncompiled_costs) != 1 or not ability.sacrifice_source or ability.zones != ("battlefield",):
        return ability
    price = variable_colored_reveal_price(ability.cost_text)
    if price is None or ability.mana_ability or not price.matches_mana(ability.mana):
        return ability
    return replace(ability, uncompiled_costs=(), variable_colored_reveal_price=price)
