"""Singular declared-attack subjects over a fixed public characteristic query."""

from dataclasses import replace
from . import regex_working_set as re

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..qualified_attack_model import (
    ATTACK_FACT_EQUAL_POWER_TOUGHNESS, ATTACK_FACT_PREFIX, QUALIFIED_ATTACK_CAPABILITY, QUALIFIED_ATTACK_VARIANT,
    OPPOSING_PLAYER_ATTACK_TRIGGER_TEMPLATE, OPPOSING_PLAYER_ATTACK_VARIANT,
    opposing_player_attack_condition, qualified_player_defense_condition,
)
from .public_event_query_conditions import public_event_query_conditions
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from ..attack_batch_model import validate_attack_characteristic_query
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison


_CONTROLLED_TOKEN_MIN_POWER = re.compile(
    r"(?P<subtype>[A-Z][A-Za-z'-]*) token you control with power (?P<minimum>[1-9][0-9]{0,2}) or greater", re.I,
)
_CONTROLLED_CREATURE_POWER = re.compile(
    r"creature you control with power (?P<power>0|[1-9][0-9]{0,2}) or (?P<comparison>less|greater)", re.I,
)
_DEFENDING_CREATURE_POWER = re.compile(
    r"(?:When|Whenever) a creature with power (?P<power>0|[1-9][0-9]{0,2}) or greater attacks you, (?P<body>.+)", re.I,
)
_OPPOSING_PLAYER_ATTACK = re.compile(
    r"Whenever a creature(?P<counter> with a counter on it)? attacks one of your opponents, (?P<body>.+)",
    re.I,
)
_ENCHANTED_OR_EQUIPPED = re.compile(
    r"(?P<article>a|another) creature you control that(?: is|['’]s) enchanted or equipped", re.I,
)


_EQUAL_POWER_AND_TOUGHNESS = re.compile(
    r"(?P<article>a|another) creature you control with power equal to its toughness", re.I,
)


def _attack_subject_query(text):
    match = re.fullmatch(r'(?P<article>an?|another) (?P<subject>.+)', text, re.I)
    if match is None: return None
    subject = match['subject']; other = match['article'].casefold() == 'another'
    token_power = _CONTROLLED_TOKEN_MIN_POWER.fullmatch(subject)
    if token_power is not None:
        subtype = canonical_creature_subtype(token_power['subtype'])
        if subtype is None:
            return None
        query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), subtypes_all=(subtype,), token=True,
            numeric_characteristic=TargetNumericCharacteristicSpec(
                TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_LEAST, int(token_power['minimum'])))
        return query, other
    creature_power = _CONTROLLED_CREATURE_POWER.fullmatch(subject)
    if creature_power is not None:
        comparison = (TargetNumericComparison.AT_MOST if creature_power['comparison'].casefold() == 'less'
            else TargetNumericComparison.AT_LEAST)
        query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
            numeric_characteristic=TargetNumericCharacteristicSpec(
                TargetNumericCharacteristic.POWER, comparison, int(creature_power['power'])))
        return query, other
    # The public static query grammar uses plural nouns; attack events supply the creature gate.
    bare = re.fullmatch(r'(?P<types>.+?) you control', subject, re.I)
    if bare:
        terms = tuple(part.strip() for part in re.split(r',?\s+or\s+|,\s*', bare['types'], flags=re.I))
        if all(canonical_creature_subtype(term) is not None for term in terms):
            subject = ' or '.join(canonical_creature_subtype(term) for term in terms) + ' creatures you control'
    subject = re.sub(r'\bcreature\b', 'creatures', subject, flags=re.I)
    counter = re.fullmatch(r'creatures you control with a (?P<name>[A-Za-z0-9+/-]+) counter on (?:it|them)', subject, re.I)
    if counter:
        query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
            state_predicate=PermanentStatePredicateSpec(counter_name=counter['name'], minimum_counter_count=1))
        parsed = ('source_controller', query, other)
    else:
        parsed = fixed_characteristic_battlefield_query_subject(('Other ' if other else '') + subject)
    if parsed is None or parsed[0] != 'source_controller': return None
    query = parsed[1]
    query = replace(query, types_all=tuple(sorted(set(query.types_all) | {'creature'})))
    try:validate_attack_characteristic_query(query)
    except ValueError:return None
    return query, parsed[2]


def qualified_attack_binding_spec(text):
    from .fixed_public_event_trigger_bindings import _spec, _all_conditions

    opposing = _OPPOSING_PLAYER_ATTACK.fullmatch(text)
    if opposing is not None:
        return _spec(
            'creature.attacks', OPPOSING_PLAYER_ATTACK_VARIANT, opposing['body'],
            OPPOSING_PLAYER_ATTACK_TRIGGER_TEMPLATE, 'trigger-event-normalized-public-action',
            condition=opposing_player_attack_condition(with_counter=opposing['counter'] is not None),
            capabilities=(QUALIFIED_ATTACK_CAPABILITY,),
        )
    defense = _DEFENDING_CREATURE_POWER.fullmatch(text)
    if defense is not None:
        return _spec('creature.attacks', QUALIFIED_ATTACK_VARIANT, defense['body'],
            'fixed-counter-public-attack-trigger-v1', 'trigger-event-normalized-public-action',
            condition=qualified_player_defense_condition(int(defense['power'])), capabilities=(QUALIFIED_ATTACK_CAPABILITY,))
    match = re.fullmatch(r'(?:When|Whenever) (?P<subject>.+?) attacks(?P<alone> alone)?, (?P<body>.+)', text, re.I)
    if match is None: return None
    subject = match['subject']
    attachment = _ENCHANTED_OR_EQUIPPED.fullmatch(subject)
    equal_stats = _EQUAL_POWER_AND_TOUGHNESS.fullmatch(subject)
    closed_subject = attachment or equal_stats
    parsed = _attack_subject_query(f"{closed_subject['article']} creature you control" if closed_subject else subject)
    if parsed is None: return None
    query, excluded = parsed
    conditions = [{'field': ATTACK_FACT_PREFIX + 'version', 'op': 'eq', 'value': 1},
        {'field': 'controller', 'op': 'eq', 'value': '$source.controller'},
        *public_event_query_conditions(query, prefix=ATTACK_FACT_PREFIX)]
    if excluded: conditions.append({'field': 'card', 'op': 'ne', 'value': '$source.ref'})
    if match['alone']: conditions.append({'field': 'attacking_alone', 'op': 'truthy', 'value': True})
    if attachment is not None:
        conditions.append({'any': [
            {'field': ATTACK_FACT_PREFIX + name, 'op': 'eq', 'value': True}
            for name in ('enchanted', 'equipped')
        ]})
    if equal_stats is not None:
        conditions.append({'field': ATTACK_FACT_EQUAL_POWER_TOUGHNESS, 'op': 'eq', 'value': True})
    for name in ('token', 'commander'):
        if getattr(query, name) is not None:
            conditions.append({'field': ATTACK_FACT_PREFIX + name, 'op': 'eq', 'value': getattr(query, name)})
    state = query.state_predicate
    if state is not None:
        for name in ('tapped', 'attacking', 'blocking', 'enchanted', 'equipped', 'modified', 'monstrous'):
            if getattr(state, name) is not None:
                conditions.append({'field': ATTACK_FACT_PREFIX + name, 'op': 'eq', 'value': getattr(state, name)})
        if state.entered_this_turn:
            conditions.append({'field': ATTACK_FACT_PREFIX + 'entered_this_turn', 'op': 'eq', 'value': True})
        if state.counter_name is not None:
            if state.minimum_counter_count != 1: return None
            conditions.append({'field': ATTACK_FACT_PREFIX + 'counter_names', 'op': 'contains_any', 'value': [state.counter_name]})
        if state.minimum_total_counter_count is not None:
            conditions.append({'field': ATTACK_FACT_PREFIX + 'total_counters', 'op': 'gte', 'value': state.minimum_total_counter_count})
    if query.numeric_characteristic is not None:
        numeric = query.numeric_characteristic
        if numeric.characteristic.value not in {'power', 'toughness'}: return None
        conditions.append({'field': ATTACK_FACT_PREFIX + numeric.characteristic.value,
            'op': 'gte' if numeric.comparison.value == 'at_least' else 'lte', 'value': numeric.value})
    return _spec('creature.attacks', QUALIFIED_ATTACK_VARIANT, match['body'],
        'fixed-counter-public-attack-trigger-v1', 'trigger-event-normalized-public-action',
        condition=_all_conditions(*conditions), capabilities=(QUALIFIED_ATTACK_CAPABILITY,))
