"""Closed actor-relative crime trigger subscriptions."""
import re
from ..crime_model import CRIME_EVENT,CRIME_CAPABILITY
from .fixed_public_event_trigger_bindings import _spec

CRIME_VARIANTS=frozenset({'controller_commits_crime','opponent_commits_crime','player_commits_crime',
    'controller_commits_crime_during_controller_turn','opponent_commits_crime_during_controller_turn',
    'player_commits_crime_during_controller_turn'})


def crime_event_binding_spec(text,*,card_name=None):
    del card_name
    match=re.fullmatch(r'Whenever (?P<actor>you|an opponent|a player) (?P<verb>commit|commits) a crime(?P<during> during your turn)?, (?P<body>.+)',text,re.I)
    if match is None or (match['actor'].casefold()=='you')!=(match['verb'].casefold()=='commit'):return None
    actor=match['actor'].casefold();conditions=[]
    graveyard=bool(re.search(r'\breturn this card from your graveyard to the battlefield\.?$',match['body'],re.I))
    if actor!='a player':conditions.append({'field':'player','op':'eq' if actor=='you' else 'ne','value':'$source.owner' if graveyard else '$source.controller'})
    variant={'you':'controller','an opponent':'opponent','a player':'player'}[actor]+'_commits_crime'
    if match['during']:
        conditions.append({'field':'active_player','op':'eq','value':'$source.controller'})
        variant+='_during_controller_turn'
    return _spec(CRIME_EVENT,variant,match['body'],'fixed-crime-event-trigger-v1','trigger-event-normalized-public-action',
        condition={'all':conditions} if len(conditions)>1 else conditions[0] if conditions else None,
        capabilities=(CRIME_CAPABILITY,),active_zone='graveyard' if graveyard else 'battlefield')
