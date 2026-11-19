"""Compile single graveyard exile with one fixed creature token result."""

from ..graveyard_exile_token_model import ExiledCreatureCardPredicate, GraveyardExileTokenSpec
from ..rules.graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec
from . import regex_working_set as re
from .token_templates import fixed_token_creation_effect_template


_CLAUSE = (
    r'Exile target card from a graveyard\. (?P<predicate>If it was a creature card|'
    r'If a creature card is exiled this way), (?P<token>create [^\n]+)'
)


def graveyard_exile_token_template(text: str):
    match = re.fullmatch(_CLAUSE, text.strip(), re.IGNORECASE)
    if match is None:
        return None
    token = fixed_token_creation_effect_template(match['token'])
    if token is None or token.target_schema is not None:
        return None
    predicate = (ExiledCreatureCardPredicate.CREATURE_CARD
        if match['predicate'].casefold() == 'if it was a creature card'
        else ExiledCreatureCardPredicate.COMMITTED_CREATURE_CARD)
    try:
        recipe = GraveyardExileTokenSpec(predicate, token.effect)
    except (TypeError, ValueError):
        return None
    target = PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CARD, owner_relation='any')
    mechanics = ('exile', 'fixed-public-zone-move', 'cr-115-targets', *token.mechanics)
    return 'graveyard-exile-fixed-creature-token-v1', (recipe.effect(),), target.to_target_schema(), mechanics
