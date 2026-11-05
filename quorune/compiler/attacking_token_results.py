"""Lower a complete attacking rider over an existing fixed creature producer."""
import re

from ..rules.attacking_token_results import ATTACKING_TOKEN_MECHANIC, ATTACKING_TOKEN_OPERATION


def fixed_attacking_token_template(text, *, compile_fixed):
    if 'attacking' not in text.casefold():
        return None
    match = re.fullmatch(r"(?P<creation>Create .+? tokens?(?: with .+?)?)(?: that(?:'s| is| are))? "
        r"(?P<tapped>tapped and )?attacking\.?", ' '.join(text.split()), re.I)
    if match is None:
        return None
    token = compile_fixed(match['creation'] + '.')
    if token is None or token.effect.get('op') != 'create_token':
        return None
    effect = dict(token.effect)
    characteristics = effect.get('characteristics')
    if (set(effect) != {'op', 'controller', 'name', 'quantity', 'characteristics'}
            or not isinstance(characteristics, dict)
            or 'creature' not in str(characteristics.get('type_line', '')).casefold().split(' — ')[0].split()):
        return None
    return type(token)(template_id='fixed-attacking-token-result-v1', effect={
        'op': ATTACKING_TOKEN_OPERATION, 'player': '$controller', 'quantity': effect['quantity'],
        'name': effect['name'], 'characteristics': characteristics,
        'tapped': bool(match['tapped']), 'token_entry_version': 1},
        mechanics=(*token.mechanics, ATTACKING_TOKEN_MECHANIC))
