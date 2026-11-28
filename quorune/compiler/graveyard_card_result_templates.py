"""Compile complete targeted graveyard-card counter instructions."""
from . import regex_working_set as re
from ..graveyard_card_result_model import GraveyardCardCounterLifeSpec, GraveyardPermanentCardCounterSpec


def parse_graveyard_card_counter_life(text):
    if re.fullmatch(r'Exile target card from a graveyard\. If it was a creature card, '
            r'put a \+1/\+1 counter on this creature and you gain 1 life\.?', text.strip(), re.IGNORECASE) is None:
        return None
    return GraveyardCardCounterLifeSpec()


def parse_graveyard_permanent_card_counter(text):
    if re.fullmatch(
        r'Exile target card from a graveyard\. If it was a permanent card, '
        r'put a \+1/\+1 counter on this permanent\.?',
        text.strip(),
        re.IGNORECASE,
    ) is None:
        return None
    return GraveyardPermanentCardCounterSpec()
