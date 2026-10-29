"""Versioned public facts of one declared attacker."""

QUALIFIED_ATTACK_CAPABILITY = 'trigger.event.qualified_attack_singular'
QUALIFIED_ATTACK_VARIANT = 'singular_qualified_attack'
ATTACK_CHARACTERISTIC_RESULT_CAPABILITY = 'resolution.event_object.attack_characteristics'
ATTACK_FACT_PREFIX = 'attacker_characteristics_'
ATTACK_FACT_COLLECTIONS = frozenset({'types', 'subtypes', 'supertypes', 'colors', 'keywords', 'counter_names'})
ATTACK_FACT_BOOLEANS = frozenset({'token', 'tapped', 'attacking', 'blocking', 'enchanted', 'equipped',
    'modified', 'monstrous', 'entered_this_turn', 'commander'})
ATTACK_FACT_NUMBERS = frozenset({'power', 'toughness', 'total_counters'})


def qualified_attack_facts_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError('Unsupported qualified-attack facts version')
    return value
