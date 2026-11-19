from __future__ import annotations

"""Closed counter and state qualifiers over existing public characteristic owners."""

from dataclasses import replace
from . import regex_working_set as re
from .fixed_numbers import fixed_number
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from ..rules.source_references import SourceReferenceSpec

_COUNT = r'a|an|one|two|three|four|five|six|seven|eight|nine|ten|[1-9]\d*'


def _plural_subject(subject):
    subject = re.sub(r'^Each (.*?)creature\b', r'\1creatures', subject, flags=re.I)
    return re.sub(r'^creature\b', 'creatures', subject, flags=re.I)


def qualified_public_query(subject, *, compile_base):
    """Add one exact qualifier without interpreting open comparisons or pronouns."""
    counter = re.fullmatch(
        rf'(?P<base>.+?) with (?P<count>{_COUNT})(?P<minimum> or more)? '
        r'(?P<counter>[A-Za-z0-9+/-]+) counters? on (?:it|them)'
        r'(?P<relation> you control| your opponents control)?', subject.strip(), re.I)
    mana = re.fullmatch(r'(?P<base>.+?) with mana value (?P<amount>\d+) or less', subject.strip(), re.I)
    match = counter or mana
    if match is None:
        return None
    base = _plural_subject(match['base'])
    if counter is not None:
        if counter['count'].casefold() not in {'a', 'an', 'one'} and not counter['minimum']:
            return None
        base += counter['relation'] or ''
    parsed = compile_base(base)
    if parsed is None or 'creature' not in parsed[1].types_all:
        return None
    relation, predicate, exclude = parsed
    if counter is not None:
        if predicate.state_predicate is not None:
            return None
        predicate = replace(predicate, state_predicate=PermanentStatePredicateSpec(
            counter_name=counter['counter'], minimum_counter_count=fixed_number(counter['count'])))
    else:
        amount = int(mana['amount'])
        if amount > 20:
            return None
        predicate = replace(predicate, permanent_mana_value_max=amount, _serialization_version=11)
    return relation, predicate, exclude


def negative_source_combat_condition(text, *, source_name):
    source = SourceReferenceSpec(source_name).regex_pattern
    if not re.fullmatch(rf"(?:it|this creature|this permanent|{source}) (?:isn't|isn’t|is not) attacking or blocking", text.strip().rstrip('.'), re.I):
        return None
    return FixedPublicStateConditionSpec(FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
        predicate=ObjectQuerySpec(zones=('battlefield',),
            state_predicate=PermanentStatePredicateSpec(attacking=False, blocking=False)), schema_version=2)


def recipient_qualified_static_handler(text):
    """A recipient condition narrows its live set, never the ability source."""
    land = re.fullmatch(r"Each land gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) as long as it(?:'s|’s| is) a creature\.", text.strip(), re.I)
    idle = re.fullmatch(r"Each untapped creature you control gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) as long as it(?:'s|’s| is) not attacking\.", text.strip(), re.I)
    match = land or idle
    if match is None:
        return None
    predicate = (ObjectQuerySpec(zones=('battlefield',), types_all=('creature', 'land')) if land else
        ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
            state_predicate=PermanentStatePredicateSpec(tapped=False, attacking=False)))
    descriptor = {'handler_id': 'continuous.anthem.fixed-query.v2', 'schema_version': 2,
        'event': 'characteristics.evaluate',
        'condition': {'target_controller': 'any' if land else 'source_controller',
            'predicate': predicate.to_dict(), 'exclude_source': False},
        'modifier': {'power': int(match['power']), 'toughness': int(match['toughness'])}}
    from ..semantic_runtime.continuous_components import FixedQueryPowerToughnessAnthemHandler
    FixedQueryPowerToughnessAnthemHandler().validate(descriptor)
    return 'continuous-recipient-qualified-fixed-anthem-v1', descriptor, ('continuous.power_toughness.fixed_anthem',)
