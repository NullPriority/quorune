"""Exact whole affected-player exile instruction and its specific-card rider."""
from . import regex_working_set as re
from ..graveyard_exile_result_model import GraveyardExileLifeResultSpec


def parse_graveyard_exile_life_result(text):
    match = re.fullmatch(r"Target (?P<subject>player|opponent) exiles (?:a|one) card from their graveyard\. "
        r"If it's a creature card, you gain (?P<amount>[1-9][0-9]*) life\.?", text.strip(), re.IGNORECASE)
    if match is None:
        return None
    return match['subject'].casefold() == 'opponent', GraveyardExileLifeResultSpec(int(match['amount']))
