"""Close a complete per-opponent rider over the ordinary fixed token producer."""
from . import regex_working_set as re

from ..attacking_opponent_model import AttackingOpponentScope, OpponentAttackDestination
from ..rules.per_opponent_attacking_tokens import (
    OPPONENT_ATTACKING_TOKEN_MECHANIC, OPPONENT_TOKEN_SCOPE_FIELD,
    per_opponent_attacking_token_closure,
)

_SCOPE = re.compile(
    r"For each opponent, (?:you )?(?P<creation>create .+? tokens?(?: with .+?)?)"
    r"(?: that(?:'s| is| are))? tapped and attacking "
    r"(?P<recipient>that player or a planeswalker they control|that player|that opponent)\.",
    re.I,
)


def per_opponent_attacking_token_template(text, *, compile_attacking):
    match = _SCOPE.fullmatch(' '.join(text.split()))
    if match is None:
        return None
    creation = match['creation']
    fixed = compile_attacking(creation[0].upper() + creation[1:] + ' tapped and attacking.')
    if fixed is None or fixed.effect.get('token_entry_version') != 1:
        return None
    destination = (OpponentAttackDestination.PLAYER_OR_PLANESWALKER
        if match['recipient'].casefold() == 'that player or a planeswalker they control'
        else OpponentAttackDestination.PLAYER)
    scope = AttackingOpponentScope(destination)
    effect = {**fixed.effect, 'token_entry_version': 3, OPPONENT_TOKEN_SCOPE_FIELD: scope.to_dict()}
    try:
        per_opponent_attacking_token_closure(effect)
    except (TypeError, ValueError, KeyError):
        return None
    return type(fixed)(template_id='fixed-per-opponent-attacking-token-v1',
        effect=effect, mechanics=(*fixed.mechanics, OPPONENT_ATTACKING_TOKEN_MECHANIC))
