"""Singular declared-attack subjects over a fixed public characteristic query."""

from dataclasses import replace
import re

from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..qualified_attack_model import QUALIFIED_ATTACK_CAPABILITY, QUALIFIED_ATTACK_VARIANT, ATTACK_FACT_PREFIX
from .public_event_query_conditions import public_event_query_conditions
from .public_state_queries import fixed_characteristic_battlefield_query_subject


def _attack_subject_query(text):
    match = re.fullmatch(r'(?P<article>an?|another) (?P<subject>.+)', text, re.I)
    if match is None: return None
    subject = match['subject']; other = match['article'].casefold() == 'another'
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
    projected = ObjectQuerySpec(zones=('battlefield',), types_all=query.types_all, types_any=query.types_any,
        excluded_types=query.excluded_types, subtypes_all=query.subtypes_all, subtypes_any=query.subtypes_any,
        excluded_subtypes=query.excluded_subtypes, colors_all=query.colors_all, colors_any=query.colors_any,
        colorless=query.colorless, supertypes_all=query.supertypes_all, keywords_all=query.keywords_all,
        keywords_none=query.keywords_none, token=query.token, state_predicate=query.state_predicate,
        numeric_characteristic=query.numeric_characteristic, commander=query.commander)
    if query != projected: return None
    return query, parsed[2]


def qualified_attack_binding_spec(text):
    from .fixed_public_event_trigger_bindings import _spec, _all_conditions
    match = re.fullmatch(r'(?:When|Whenever) (?P<subject>.+?) attacks(?P<alone> alone)?, (?P<body>.+)', text, re.I)
    if match is None: return None
    parsed = _attack_subject_query(match['subject'])
    if parsed is None: return None
    query, excluded = parsed
    conditions = [{'field': ATTACK_FACT_PREFIX + 'version', 'op': 'eq', 'value': 1},
        {'field': 'controller', 'op': 'eq', 'value': '$source.controller'},
        *public_event_query_conditions(query, prefix=ATTACK_FACT_PREFIX)]
    if excluded: conditions.append({'field': 'card', 'op': 'ne', 'value': '$source.ref'})
    if match['alone']: conditions.append({'field': 'attacking_alone', 'op': 'truthy', 'value': True})
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
