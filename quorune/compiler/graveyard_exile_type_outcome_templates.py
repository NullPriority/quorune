"""Compile three closed outcomes that can share a graveyard card's types."""

from ..rules.graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec
from ..rules.graveyard_exile_type_outcomes import GraveyardExileTypeOutcomeSpec
from . import regex_working_set as re
from .token_templates import fixed_token_creation_effect_template


_CLAUSES = (
    r'Exile target card from a graveyard\. If it was a creature card, (?P<creature>create [^.]+\.) '
    r'If it was a land card, (?P<land>create a Treasure token\.) Otherwise, you gain 3 life\.'
)


def graveyard_exile_type_outcome_template(text: str):
    match = re.fullmatch(_CLAUSES, text.strip(), re.IGNORECASE)
    if match is None:
        return None
    creature = fixed_token_creation_effect_template(match['creature'])
    treasure = fixed_token_creation_effect_template(match['land'])
    if creature is None or treasure is None or creature.target_schema is not None or treasure.target_schema is not None:
        return None
    try:
        result = GraveyardExileTypeOutcomeSpec(creature.effect, treasure.effect)
    except (TypeError, ValueError):
        return None
    target = PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CARD, owner_relation='any')
    mechanics = tuple(dict.fromkeys(('exile', 'fixed-public-zone-move', 'cr-115-targets',
        *creature.mechanics, *treasure.mechanics, 'cr-119-life')))
    return 'graveyard-exile-creature-and-land-outcomes-v1', (result.effect(),), target.to_target_schema(), mechanics
