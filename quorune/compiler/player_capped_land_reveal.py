"""Compile a complete X spell whose own text fixes the announcement cap."""

from . import regex_working_set as re
from ..reveal_until_library_selection_model import REVEAL_UNTIL_MECHANIC, player_capped_land_reveal_effect


def player_capped_land_reveal_template(material, *, mana_cost):
    if mana_cost != "{X}{G}{G}":
        return None
    if not re.fullmatch(
        r"X can['’]t be greater than the number of players in the game\. "
        r"Reveal cards from the top of your library until you reveal X land cards\. "
        r"Put those land cards onto the battlefield tapped and the rest on the bottom of your library in a random order\.",
        material, re.IGNORECASE,
    ):
        return None
    return "player-capped-reveal-until-lands-v3", (player_capped_land_reveal_effect(),), None, (REVEAL_UNTIL_MECHANIC,)
