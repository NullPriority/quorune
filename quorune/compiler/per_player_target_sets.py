"""One optional permanent per player over existing announced target-set owners."""
from copy import deepcopy
import re
from .any_number_target_sets import any_number_target_set_template

PER_PLAYER_TARGET_CAPABILITY='target.set.one_per_player'
PER_PLAYER_TARGET_MECHANIC='fixed-per-player-target-set'


def per_player_target_set_template(text):
    match=re.fullmatch(r'For each (?P<players>opponent|player), (?P<body>.+)',text.strip(),re.I)
    if match is None:return None
    body=match['body'];qualifiers=tuple(re.finditer(r'\bthat (?P<who>player|opponent) controls\b',body,re.I))
    if len(qualifiers)!=1 or len(re.findall(r'\bup to one target\b',body,re.I))!=1:return None
    qualifier=qualifiers[0]
    if match['players'].casefold()=='player' and qualifier['who'].casefold()=='opponent':return None
    relation='opponent' if match['players'].casefold()=='opponent' else 'any'
    subject=body[:qualifier.start()]+('an opponent controls' if relation=='opponent' else '')+body[qualifier.end():]
    prototype=re.sub(r'\bup to one target\b','any number of target',subject,flags=re.I)
    compiled=any_number_target_set_template(prototype)
    if compiled is None or compiled[2].get('zones')!=['battlefield'] or compiled[2].get('categories')!=['permanent']:return None
    schema=deepcopy(dict(compiled[2]))
    if schema.get('controller_relation','any')!=relation:return None
    schema.update(distinct_controllers=True,retain_controller=True)
    return (compiled[0]+'-per-player',compiled[1],schema,tuple(dict.fromkeys((*compiled[3],PER_PLAYER_TARGET_MECHANIC))))
