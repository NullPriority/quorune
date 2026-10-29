"""Fixed matching-attack counts with explicit declaration or recipient grouping."""

import re
from dataclasses import replace

from ..attack_batch_model import (QualifiedAttackBatchSpec,QUALIFIED_ATTACK_BATCH_CAPABILITY,
    QUALIFIED_ATTACK_BATCH_VARIANT,ATTACK_BATCH_CONDITION)
from ..object_predicate import ObjectQuerySpec,PermanentStatePredicateSpec
from ..target_numeric import TargetNumericCharacteristicSpec,TargetNumericCharacteristic,TargetNumericComparison
from .fixed_numbers import fixed_number,FIXED_COUNT_PATTERN
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from ..creature_subtypes import canonical_creature_subtype_surface


def _batch_query(subject):
    counters=re.fullmatch(r'(?P<body>.+?) with (?:a |one or more )?counters? on (?:it|them)',subject,re.I)
    if counters is not None:
        parsed=_batch_query(counters['body'])
        if parsed is None or parsed[0].state_predicate is not None:return None
        return replace(parsed[0],state_predicate=PermanentStatePredicateSpec(minimum_total_counter_count=1)),parsed[1]
    token=re.fullmatch(r'(?P<token>nontoken|token) (?P<body>.+)',subject,re.I)
    if token is not None:
        parsed=_batch_query(token['body'])
        return None if parsed is None else (replace(parsed[0],token=token['token'].casefold()=='token'),parsed[1])
    numeric=re.fullmatch(r'(?P<body>.+?) with (?P<stat>power|toughness) (?P<n>[0-9]+) or (?P<bound>greater|less)',subject,re.I)
    if numeric is not None:
        query=_batch_query(numeric['body'])
        return None if query is None else (replace(query[0],numeric_characteristic=TargetNumericCharacteristicSpec(
            TargetNumericCharacteristic(numeric['stat'].casefold()),TargetNumericComparison.AT_LEAST if numeric['bound'].casefold()=='greater' else TargetNumericComparison.AT_MOST,int(numeric['n']))),query[1])
    parsed=fixed_characteristic_battlefield_query_subject(subject+' you control')
    if parsed is None:
        terms=tuple(part.strip() for part in re.split(r',?\s+or\s+|,\s*',subject,flags=re.I))
        if terms and all(canonical_creature_subtype_surface(term) is not None for term in terms):
            parsed=fixed_characteristic_battlefield_query_subject(' or '.join(canonical_creature_subtype_surface(term) for term in terms)+' creatures you control')
    if parsed is None:
        # Counter-qualified public sets print their controller before the qualifier.
        controlled=re.sub(r'^(creatures)( with .+)$',r'\1 you control\2',subject,flags=re.I)
        parsed=fixed_characteristic_battlefield_query_subject(controlled)
    if parsed is None or parsed[0]!='source_controller':return None
    return replace(parsed[1],types_all=tuple(sorted(set(parsed[1].types_all)|{'creature'}))),parsed[2]


def qualified_attack_batch_binding_spec(text):
    from .fixed_public_event_trigger_bindings import _spec,_all_conditions
    count=rf'(?P<count>{FIXED_COUNT_PATTERN})'
    match=re.fullmatch(rf'Whenever you attack(?P<recipient> a player or planeswalker)? with {count} or more (?P<subject>.+?), (?P<body>.+)',text,re.I)
    if match is not None:
        grouping='player_or_planeswalker' if match['recipient'] else 'declaration'
    else:
        match=re.fullmatch(rf'Whenever {count} or more (?P<subject>.+?) you control attack(?P<recipient> a player)?, (?P<body>.+)',text,re.I)
        if match is None:return None
        grouping='player' if match['recipient'] else 'declaration'
    try:minimum=fixed_number(match['count'])
    except (KeyError,ValueError):return None
    parsed=_batch_query(match['subject'])
    if parsed is None:return None
    if re.match(r'(?:it|that creature|that permanent)\b',match['body'],re.I):return None
    try:spec=QualifiedAttackBatchSpec(parsed[0],minimum,grouping,parsed[1])
    except (ValueError,TypeError):return None
    condition={'field':ATTACK_BATCH_CONDITION,'op':'gte','value':minimum,'attack_batch':spec.to_dict()}
    return _spec('creature.attacks',QUALIFIED_ATTACK_BATCH_VARIANT,match['body'],
        'fixed-counter-public-attack-trigger-v1','trigger-event-normalized-public-action',
        condition=_all_conditions({'field':'controller','op':'eq','value':'$source.controller'},condition),
        capabilities=(QUALIFIED_ATTACK_BATCH_CAPABILITY,))
