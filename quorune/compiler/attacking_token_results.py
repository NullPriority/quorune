"""Lower a complete attacking rider over an existing fixed creature producer."""
from . import regex_working_set as re

from ..rules.attacking_token_results import ATTACKING_TOKEN_MECHANIC, ATTACKING_TOKEN_OPERATION


_ENTRY_COPY_BODY = ("create a token that's a copy of another target attacking creature. "
                    "the token enters tapped and attacking.")


def attacking_entry_copy_binding(text, *, card_name):
    from ..rules.attacking_entry_copy import CAPABILITY, condition
    from .fixed_public_event_trigger_bindings import _source_subject, _spec

    if not card_name or "if it's attacking" not in text.casefold().replace("’", "'"):
        return None
    match = re.fullmatch(r"When (?P<source>.+?) enters from your hand, if it['’]s attacking, (?P<body>.+)",
                         text, re.I)
    if match is None or not _source_subject(match["source"], card_name=card_name):
        return None
    body = " ".join(match["body"].split()).casefold().replace("’", "'")
    if body != _ENTRY_COPY_BODY:
        return None
    return _spec("permanent.enter", "attacking_entry_copy", match["body"], "attacking-entry-target-copy-v1",
                 "trigger-event-normalized-zone-change", condition=condition(), capabilities=(CAPABILITY,))


def attacking_entry_copy_result(binding, body):
    if binding.variant != "attacking_entry_copy" or body != binding.body:
        return None
    from ..rules.attacking_entry_copy import effect, target_schema

    return (binding.template_id, (effect(),), target_schema(), (ATTACKING_TOKEN_MECHANIC, "cr-111-tokens", "cr-115-targets"))


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
