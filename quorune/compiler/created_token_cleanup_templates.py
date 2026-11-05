"""A complete cleanup clause over an already-closed ordinary token producer."""
import re
from ..created_token_cleanup_model import CreatedTokenCleanupSpec, TOKEN_CLEANUP_MECHANIC


def _cleanup_parts(text):
    if not re.search(r'\b(?:exile|sacrifice)\b', text, re.I):
        return None
    match = re.fullmatch(r'(?P<creation>Create .+?)\. (?P<action>Exile|Sacrifice) '
        r'(?:it|them|that token|those tokens|the tokens?) at '
        r'(?P<timing>the beginning of (?P<owner>the|your) next end step|end of combat)\.?',
        ' '.join(text.split()), re.I)
    if match is None:
        return None
    timing = ('end_of_combat' if match['timing'].casefold() == 'end of combat'
        else 'controller_next_end_step' if match['owner'].casefold() == 'your' else 'next_end_step')
    return match['creation'], CreatedTokenCleanupSpec(match['action'].casefold(), timing)


def _effect_with_cleanup(effect, spec):
    result = {**dict(effect), 'token_cleanup': spec.to_dict()}
    if result['op'] == 'choose_fixed_attacking_token_destinations':
        result['token_entry_version'] = 2
    return result


def created_token_cleanup_template(text, *, compile_inner):
    parts = _cleanup_parts(text)
    if parts is None:
        return None
    creation, spec = parts
    token = compile_inner(creation + '.')
    if token is None or token.target_schema is not None or token.effect.get('op') not in {
            'create_token', 'choose_fixed_attacking_token_destinations'} or 'copy_of' in token.effect or 'copy_spec' in token.effect:
        return None
    if 'token_cleanup' in token.effect:
        return None
    return type(token)(template_id=token.template_id+'-cleanup-v1', effect=_effect_with_cleanup(token.effect, spec),
        mechanics=tuple(dict.fromkeys((*token.mechanics, TOKEN_CLEANUP_MECHANIC))))


def scalar_created_token_cleanup_template(text, *, compile_inner, **context):
    parts = _cleanup_parts(text)
    if parts is None:
        return None
    creation, spec = parts
    compiled = compile_inner(creation + '.', **context)
    if compiled is None or compiled[0] is None:
        return None
    template, effects, target, mechanics = compiled
    if (target is not None or len(effects) != 1 or effects[0].get('op') not in {
            'create_token', 'choose_fixed_attacking_token_destinations'}
            or any(key in effects[0] for key in ('copy_of', 'copy_spec', 'token_cleanup'))):
        return None
    return template+'-cleanup-v1', (_effect_with_cleanup(effects[0], spec),), None, tuple(
        dict.fromkeys((*mechanics, TOKEN_CLEANUP_MECHANIC)))
