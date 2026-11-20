"""Versioned public facts of one declared attacker."""

QUALIFIED_ATTACK_CAPABILITY = 'trigger.event.qualified_attack_singular'
QUALIFIED_ATTACK_VARIANT = 'singular_qualified_attack'
ATTACK_CHARACTERISTIC_RESULT_CAPABILITY = 'resolution.event_object.attack_characteristics'
ATTACK_FACT_PREFIX = 'attacker_characteristics_'
ATTACK_FACT_EQUAL_POWER_TOUGHNESS = ATTACK_FACT_PREFIX + 'equal_power_toughness'
ATTACK_FACT_COLLECTIONS = frozenset({'types', 'subtypes', 'supertypes', 'colors', 'keywords', 'counter_names'})
ATTACK_FACT_BOOLEANS = frozenset({'token', 'tapped', 'attacking', 'blocking', 'enchanted', 'equipped',
    'modified', 'monstrous', 'entered_this_turn', 'commander'})
ATTACK_FACT_NUMBERS = frozenset({'power', 'toughness', 'total_counters'})
QUALIFIED_ATTACK_FULL_STATS_VERSION = 2
ATTACK_RECIPIENT_KIND_FIELD = 'attacked_recipient_kind'


def declared_attacker_has_equal_power_and_toughness(context) -> bool:
    power = context.get(ATTACK_FACT_PREFIX + 'power')
    toughness = context.get(ATTACK_FACT_PREFIX + 'toughness')
    return type(power) is int and type(toughness) is int and power == toughness


def qualified_player_defense_condition(minimum_power: int):
    if type(minimum_power) is not int or minimum_power < 0:
        raise ValueError('A defending-player attack requires a fixed nonnegative power threshold')
    return {'all': [
        {'field': ATTACK_FACT_PREFIX + 'version', 'op': 'eq', 'value': 1},
        {'field': 'defending_player', 'op': 'eq', 'value': '$source.controller'},
        {'field': ATTACK_RECIPIENT_KIND_FIELD, 'op': 'eq', 'value': 'player'},
        {'field': ATTACK_FACT_PREFIX + 'types', 'op': 'contains_any', 'value': ['creature']},
        {'field': ATTACK_FACT_PREFIX + 'power', 'op': 'gte', 'value': minimum_power},
    ]}


def defended_player_or_planeswalker_condition():
    return {'all': [
        {'field': 'defending_player', 'op': 'eq', 'value': '$source.controller'},
        {'field': ATTACK_RECIPIENT_KIND_FIELD, 'op': 'in', 'value': ['player', 'planeswalker']},
    ]}


def source_attacks_battle_condition():
    return {"all": [
        {"field": ATTACK_FACT_PREFIX + "version", "op": "eq", "value": 1},
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": ATTACK_RECIPIENT_KIND_FIELD, "op": "eq", "value": "battle"},
    ]}


def qualified_attack_facts_version(value):
    if value is not None and (type(value) is not int or value not in {1, QUALIFIED_ATTACK_FULL_STATS_VERSION}):
        raise ValueError('Unsupported qualified-attack facts version')
    return value
