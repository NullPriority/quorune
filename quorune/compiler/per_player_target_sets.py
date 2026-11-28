"""One optional permanent per player over existing announced target-set owners."""
from copy import deepcopy
from . import regex_working_set as re
from .any_number_target_sets import any_number_target_set_template

PER_PLAYER_TARGET_CAPABILITY='target.set.one_per_player'
PER_PLAYER_TARGET_MECHANIC='fixed-per-player-target-set'


def paired_controller_return_template(text):
    from .fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template

    if " ".join(text.casefold().replace("’", "'").split()) != (
        "choose two target creatures controlled by different players. return those creatures to their owners' hands."
    ):
        return None
    template = fixed_homogeneous_target_set_effect_template("Return two target creatures to their owners' hands.")
    template_id, effects, schema, mechanics = template.compiled()
    return template_id + "-distinct-controllers", effects, {**schema, "distinct_controllers": True}, mechanics


def per_player_target_set_template(text):
    from .fixed_control_templates import targeted_control_set_template

    if compiled := (paired_controller_return_template(text) or targeted_control_set_template(text, per_opponent=True)):
        return compiled
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


def controlled_destruction_source_counter_template(text):
    from ..rules.controlled_destruction_source_counters import (
        SOURCE_COUNTER_REWARD_MECHANIC, SOURCE_COUNTER_REWARD_TEMPLATE,
        controlled_destruction_source_counter_effect, creature_target_role_schema,
    )

    if text.strip().casefold() != (
        "for each player, destroy up to one target creature that player controls. "
        "if a creature you controlled was destroyed this way, put two +1/+1 counters on this vehicle."
    ):
        return None
    return (
        SOURCE_COUNTER_REWARD_TEMPLATE, (controlled_destruction_source_counter_effect(),),
        creature_target_role_schema(), (PER_PLAYER_TARGET_MECHANIC, SOURCE_COUNTER_REWARD_MECHANIC, "cr-115-targets"),
    )
