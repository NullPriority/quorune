"""Seal public attack qualifications and require their declared capability."""

from collections.abc import Mapping
from types import SimpleNamespace

from ..object_query import exact_numeric_characteristic
from ..qualified_attack_model import (
    ATTACK_FACT_PREFIX, ATTACK_FACT_COLLECTIONS, ATTACK_FACT_BOOLEANS, ATTACK_FACT_NUMBERS,
    ATTACK_FACT_EQUAL_POWER_TOUGHNESS,
    ATTACK_RECIPIENT_KIND_FIELD, QUALIFIED_ATTACK_CAPABILITY, QUALIFIED_ATTACK_FULL_STATS_VERSION,
    defended_player_condition, defended_player_or_planeswalker_condition,
    qualified_attack_facts_version, qualified_player_defense_condition,
    OPPOSING_PLAYER_ATTACK_PROGRAM_TEMPLATE, opposing_player_attack_condition, source_attacks_battle_condition,
)
from ..replacement.immutable import FrozenMap, thaw_value


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


def qualified_player_defense_is_closed(condition) -> bool:
    if not isinstance(condition, Mapping) or set(condition) != {'all'}:
        return False
    clauses = condition['all']
    if not isinstance(clauses, (list, tuple)) or len(clauses) != 5 or not isinstance(clauses[-1], Mapping):
        return False
    minimum = clauses[-1].get('value')
    return type(minimum) is int and minimum >= 0 and FrozenMap(condition) == FrozenMap(qualified_player_defense_condition(minimum))


def declared_defender_scope_is_closed(condition) -> bool:
    return isinstance(condition, Mapping) and thaw_value(condition) in (
        defended_player_condition(), defended_player_or_planeswalker_condition(),
    )


def _opposing_player_attack_is_closed(condition) -> bool:
    if not isinstance(condition, Mapping):
        return False
    raw = thaw_value(condition)
    return raw == opposing_player_attack_condition() or (
        raw == opposing_player_attack_condition(with_counter=True)
        and all(type(row["value"]) is int for row in raw["all"][2:])
    )


def qualified_attack_runtime_dependencies(program):
    from .event_subscriptions import FixedEventSubscriptionSet

    opposing = _opposing_player_attack_is_closed(program.event_condition)
    opposing_template = (getattr(program, "provenance", {}).get("template_id")
                         == OPPOSING_PLAYER_ATTACK_PROGRAM_TEMPLATE)
    if opposing or opposing_template:
        if not opposing or not opposing_template or (
            (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None
        ):
            raise ValueError("Opposing player attacks require the exact singular declared opponent frame")
        return (QUALIFIED_ATTACK_CAPABILITY,)
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
                battle_scope = source_attacks_battle_condition()
                if thaw_value(value) == battle_scope["all"][-1]:
                    if program.event != "creature.attacks" or thaw_value(program.event_condition) != battle_scope:
                        raise ValueError("A battle-attack condition requires the source's own declared attack")
                elif thaw_value(value) == defended_player_condition()['all'][1]:
                    if program.event != 'creature.attacks':
                        raise ValueError('The player recipient requires an actual declared attack')
                    if thaw_value(program.event_condition) == defended_player_condition():
                        recipient_scope = defended_player_condition()
                    elif not qualified_player_defense_is_closed(program.event_condition):
                        raise ValueError('Attacks on the source controller require an exact player-recipient filter')
                else:
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
                elif field == ATTACK_FACT_EQUAL_POWER_TOUGHNESS:
                    if op != 'eq' or expected is not True:
                        raise ValueError('Equal attacker power and toughness requires the closed comparison')
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


def qualified_attack_defender_condition_is_closed(event,condition):
    """One controlled, versioned attack occurrence supplies one defending player."""
    from .event_subscriptions import FixedEventSubscriptionSet
    if event!='creature.attacks':return False
    try:
        if FixedEventSubscriptionSet.from_condition(condition) is not None:return False
        required=qualified_attack_runtime_dependencies(SimpleNamespace(event=event,event_condition=condition))
        return QUALIFIED_ATTACK_CAPABILITY in required and _has_defended_recipient_scope(thaw_value(condition),
            {'field':'controller','op':'eq','value':'$source.controller'})
    except (TypeError,ValueError,KeyError):return False
