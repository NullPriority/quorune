"""Exact affected-player graveyard choice; the chosen card is not a target."""
from . import regex_working_set as re
from ..targeted_graveyard_exile_model import (
    GraveyardExileChoiceSpec, GRAVEYARD_EXILE_CHOICE_MECHANIC,
)
from ..rules.targeted_graveyard_exile import graveyard_exile_player_target
from .graveyard_exile_result_templates import parse_graveyard_exile_life_result


def targeted_graveyard_exile_template(text):
    result = parse_graveyard_exile_life_result(text)
    if result is not None:
        opponent, consequence = result
        return ('target-player-graveyard-exile-choice-result-v2',
            (GraveyardExileChoiceSpec(schema_version=2, result=consequence).effect(),),
            graveyard_exile_player_target(opponent=opponent),
            (GRAVEYARD_EXILE_CHOICE_MECHANIC, 'cr-115-targets'))
    match = re.fullmatch(r'Target (?P<subject>player|opponent) exiles (?:a|one) card '
        r'from their graveyard\.?', text.strip(), re.IGNORECASE)
    if match is None:
        return None
    opponent = match['subject'].casefold() == 'opponent'
    return ('target-player-graveyard-exile-choice-v1', (GraveyardExileChoiceSpec().effect(),),
        graveyard_exile_player_target(opponent=opponent),
        (GRAVEYARD_EXILE_CHOICE_MECHANIC, 'cr-115-targets'))
