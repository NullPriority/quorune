"""Seal public attack qualifications and require their declared capability."""

from collections.abc import Mapping
from types import SimpleNamespace

from ..qualified_attack_model import (ATTACK_FACT_PREFIX, ATTACK_FACT_COLLECTIONS,
    ATTACK_FACT_BOOLEANS, ATTACK_FACT_NUMBERS, ATTACK_RECIPIENT_KIND_FIELD, QUALIFIED_ATTACK_CAPABILITY,
    QUALIFIED_ATTACK_FULL_STATS_VERSION, defended_player_or_planeswalker_condition, qualified_attack_facts_version)
from ..object_query import exact_numeric_characteristic
from ..replacement.immutable import thaw_value


def attack_characteristic_context(host, card):
    """Read after declaration; callers seal all occurrences before discovery."""
    version = qualified_attack_facts_version(getattr(host.state, 'qualified_attack_facts_version', None))
    if version is None:
        return {}
    row = host._public_object_query_result(card)
    power, toughness = row.effective_power, row.effective_toughness
    if version == QUALIFIED_ATTACK_FULL_STATS_VERSION:
        effective = host._effective_card_data(card)
        power = exact_numeric_characteristic(card, effective, "power")
        toughness = exact_numeric_characteristic(card, effective, "toughness")
    facts = {name: list(getattr(row, name)) for name in ATTACK_FACT_COLLECTIONS - {'counter_names'}}
    facts.update({name: getattr(row, name) for name in ATTACK_FACT_BOOLEANS - {'monstrous'}})
    facts.update(version=1, power=power, toughness=toughness,
        monstrous=row.monstrous_value is not None,
        counter_names=sorted(name for name, amount in row.counters.items() if amount > 0),
        total_counters=sum(row.counters.values()))
    return {ATTACK_FACT_PREFIX + name: value for name, value in facts.items()}


def _has_defended_recipient_scope(condition, expected):
    if condition == expected:
        return True
    return (isinstance(condition, Mapping) and set(condition) == {'all'}
            and isinstance(condition['all'], (list, tuple))
            and any(_has_defended_recipient_scope(child, expected) for child in condition['all']))


def qualified_attack_runtime_dependencies(program):
    from .event_subscriptions import FixedEventSubscriptionSet
    subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
    if subscriptions is not None:
        required=set()
        for subscription in subscriptions.subscriptions:
            branch=SimpleNamespace(event=subscription.event,event_condition=subscription.condition)
            required.update(qualified_attack_runtime_dependencies(branch))
        return tuple(sorted(required))
    found = False
    version = False
    recipient_scope = None
    def visit(value):
        nonlocal found, version, recipient_scope
        if isinstance(value, Mapping):
            field = value.get('field')
            if field == ATTACK_RECIPIENT_KIND_FIELD:
                recipient_scope = defended_player_or_planeswalker_condition()
                if thaw_value(value) != recipient_scope['all'][1]:
                    raise ValueError('Declared defender kind requires the closed player-or-planeswalker filter')
            if isinstance(field, str) and field.startswith(ATTACK_FACT_PREFIX):
                found = True
                if set(value) != {'field', 'op', 'value'}:
                    raise ValueError('Attacker characteristic conditions have a closed field set')
                name = field.removeprefix(ATTACK_FACT_PREFIX); op = value['op']; expected = value['value']
                if name == 'version':
                    if op != 'eq' or type(expected) is not int or expected != 1:
                        raise ValueError('Attacker characteristics require version1')
                    version = True
                elif name in ATTACK_FACT_COLLECTIONS:
                    if op not in {'contains_any', 'eq'} or not isinstance(expected, (tuple, list)) or any(type(v) is not str for v in expected):
                        raise ValueError('Attacker collection conditions require fixed string sets')
                elif name in ATTACK_FACT_BOOLEANS:
                    if op != 'eq' or type(expected) is not bool:
                        raise ValueError('Attacker state conditions require exact booleans')
                elif name in ATTACK_FACT_NUMBERS:
                    if op not in {'gte', 'lte', 'eq'} or type(expected) is not int:
                        raise ValueError('Attacker numeric conditions require fixed integer comparisons')
                else:
                    raise ValueError('Unknown attacker characteristic field')
            for child in value.values(): visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value: visit(child)
    visit(program.event_condition)
    if found and (not version or program.event != 'creature.attacks'):
        raise ValueError('Qualified attacker conditions require a versioned declared-attack occurrence')
    if recipient_scope is not None and (program.event != 'creature.attacks'
            or not _has_defended_recipient_scope(thaw_value(program.event_condition), recipient_scope)):
        raise ValueError('Declared defender kind requires the source controller as the defending player')
    return (QUALIFIED_ATTACK_CAPABILITY,) if found or recipient_scope is not None else ()
