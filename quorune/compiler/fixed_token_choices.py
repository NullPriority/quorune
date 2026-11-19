"""Mandatory resolution choices among existing fixed predefined token definitions."""
from . import regex_working_set as re

from ..fixed_token_production import FixedTokenCreationTemplate

TOKEN_CHOICE_MECHANIC = 'fixed-token-definition-choice'
TOKEN_CHOICE_CAPABILITY = 'token.creation.fixed_definition_choice'
TOKEN_CHOICE_PROMPT = 'Choose a token to create.'


def fixed_token_choice_template(text, *, compile_fixed):
    match = re.fullmatch(r'Create (?:your choice of )?(?P<choices>.+?)\.',
        ' '.join(text.strip().split()),re.I)
    if match is None:return None
    head_tail = re.split(r'\s+or\s+',match['choices'],flags=re.I)
    if len(head_tail)!=2:return None
    parts = [*head_tail[0].removesuffix(',').split(','),head_tail[1]]
    if not 2<=len(parts)<=4:return None
    options,branches,mechanics = [],{},[TOKEN_CHOICE_MECHANIC]
    for part in parts:
        option = re.fullmatch(r'an? (?P<name>[A-Za-z]+) token',' '.join(part.split()),re.I)
        if option is None:return None
        name = option['name'].title()
        if name in branches:return None
        compiled = compile_fixed(f'Create a {name} token.')
        if compiled is None or compiled.target_schema is not None:return None
        options.append({'id':name,'label':name})
        branches[name] = [dict(compiled.effect)]
        mechanics.extend(compiled.mechanics)
    return FixedTokenCreationTemplate('fixed-token-definition-choice-v1',
        {'op':'choose_option','player':'$controller','prompt':TOKEN_CHOICE_PROMPT,
         'options':options,'then_by_choice':branches},tuple(dict.fromkeys(mechanics)))
