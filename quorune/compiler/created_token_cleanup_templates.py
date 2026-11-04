"""A complete cleanup clause over an already-closed ordinary token producer."""
import re
from ..created_token_cleanup_model import CreatedTokenCleanupSpec, TOKEN_CLEANUP_MECHANIC


def created_token_cleanup_template(text, *, compile_inner):
    if not re.search(r'\b(?:exile|sacrifice)\b', text, re.I):
        return None
    match = re.fullmatch(r'(?P<creation>Create .+?)\. (?P<action>Exile|Sacrifice) '
        r'(?:it|them|that token|those tokens|the tokens?) at '
        r'(?P<timing>the beginning of (?P<owner>the|your) next end step|end of combat)\.?',
        ' '.join(text.split()), re.I)
    if match is None:
        return None
    token = compile_inner(match['creation'] + '.')
    if token is None or token.target_schema is not None or token.effect.get('op') not in {
            'create_token', 'choose_fixed_attacking_token_destinations'} or 'copy_of' in token.effect or 'copy_spec' in token.effect:
        return None
    if 'token_cleanup' in token.effect:
        return None
    timing = ('end_of_combat' if match['timing'].casefold() == 'end of combat'
        else 'controller_next_end_step' if match['owner'].casefold() == 'your' else 'next_end_step')
    effect = {**dict(token.effect), 'token_cleanup': CreatedTokenCleanupSpec(match['action'].casefold(), timing).to_dict()}
    if effect['op'] == 'choose_fixed_attacking_token_destinations':
        effect['token_entry_version'] = 2
    return type(token)(template_id=token.template_id+'-cleanup-v1', effect=effect,
        mechanics=tuple(dict.fromkeys((*token.mechanics, TOKEN_CLEANUP_MECHANIC))))
