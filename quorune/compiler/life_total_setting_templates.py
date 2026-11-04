"""Complete source clauses for fixed and resolution-count life totals."""
import re
from ..life_total_setting_model import LifeTotalValueSpec
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number


def life_total_setting_template(text):
    from .life_templates import FixedLifeEffectTemplate,LIFE_MECHANIC,_player_target_schema
    text=' '.join(text.strip().split())
    counted=re.fullmatch(r'Count the number of (?P<objects>cards in your library|permanents you control)\. Your life total becomes that number\.?',text,re.I)
    if counted is not None:
        value=LifeTotalValueSpec('library' if counted['objects'].casefold().startswith('cards') else 'permanents')
        return FixedLifeEffectTemplate('set-controller-counted-life-total-v1',
            {'op':'set_life_total','schema_version':1,'player':'$controller','value':value.to_dict()})
    match=re.fullmatch(r"(?P<subject>Your|Target player's|Target opponent's|Each player's) life total becomes (?P<value>.+?)\.?",text,re.I)
    if match is None:return None
    raw=match['value'];subject=match['subject'].casefold()
    if re.fullmatch(FIXED_COUNT_PATTERN,raw,re.I):value=LifeTotalValueSpec('fixed',fixed_number(raw))
    elif raw.casefold()=='equal to your starting life total' and subject=='your':value=LifeTotalValueSpec('starting')
    elif raw.casefold() in {'the highest life total among all players','the lowest life total among all players'}:
        value=LifeTotalValueSpec('highest' if 'highest' in raw.casefold() else 'lowest')
    elif raw.casefold()=='the number of creatures they control' and subject=="each player's":value=LifeTotalValueSpec('creatures')
    else:return None
    mechanics=[LIFE_MECHANIC];target=None
    if subject=="each player's":
        recipients={'scope':'all_active_players'};mechanics.append('cr-101-the-magic-golden-rules')
    elif subject=='your':recipients={'player':'$controller'}
    else:
        recipients={'player':'$target.0'};target=_player_target_schema('opponent' if subject=="target opponent's" else 'any')
        mechanics.append('cr-115-targets')
    return FixedLifeEffectTemplate('set-life-total-'+value.kind+'-v1',
        {'op':'set_life_total','schema_version':1,**recipients,'value':value.to_dict()},target,tuple(mechanics))
