from __future__ import annotations

"""Exact static subjects; no card identity participates in this production."""

from ..graveyard_exile_model import GraveyardExileSpec,GraveyardExileScope,GRAVEYARD_EXILE_DEPENDENCIES


def graveyard_exile_template(text):
    material=' '.join(text.strip().replace('’',"'").split()).casefold()
    scopes={
        'if a card or token would be put into a graveyard from anywhere, exile it instead.':GraveyardExileScope.CARD_OR_TOKEN,
        "if a card would be put into an opponent's graveyard from anywhere, exile it instead.":GraveyardExileScope.OPPONENT_CARD,
        'if a permanent would be put into a graveyard, exile it instead.':GraveyardExileScope.PERMANENT,
        "if a nontoken creature an opponent owns would die or a creature card not on the battlefield would be put into an opponent's graveyard, exile that card instead.":GraveyardExileScope.OPPONENT_CREATURE_CARD,
        'if an instant or sorcery card would be put into a graveyard from anywhere, exile it instead.':GraveyardExileScope.INSTANT_OR_SORCERY_CARD,
        'if a creature an opponent controls would die, exile it instead.':GraveyardExileScope.OPPONENT_CONTROLLED_CREATURE,
    }
    scope=scopes.get(material)
    if scope is None:return None
    spec=GraveyardExileSpec(scope)
    return ('qualified-graveyard-exile-v1',spec.descriptor(),GRAVEYARD_EXILE_DEPENDENCIES)
