"""Seal public attack qualifications and require their declared capability."""

from collections.abc import Mapping

from ..qualified_attack_model import (ATTACK_FACT_PREFIX, ATTACK_FACT_COLLECTIONS,
    ATTACK_FACT_BOOLEANS, ATTACK_FACT_NUMBERS, QUALIFIED_ATTACK_CAPABILITY, qualified_attack_facts_version)


def attack_characteristic_context(host, card):
    """Read after declaration; callers seal all occurrences before discovery."""
    if qualified_attack_facts_version(getattr(host.state, 'qualified_attack_facts_version', None)) is None:
        return {}
    row = host._public_object_query_result(card)
    facts = {name: list(getattr(row, name)) for name in ATTACK_FACT_COLLECTIONS - {'counter_names'}}
    facts.update({name: getattr(row, name) for name in ATTACK_FACT_BOOLEANS - {'monstrous'}})
    facts.update(version=1, power=row.effective_power, toughness=row.effective_toughness,
        monstrous=row.monstrous_value is not None,
        counter_names=sorted(name for name, amount in row.counters.items() if amount > 0),
        total_counters=sum(row.counters.values()))
    return {ATTACK_FACT_PREFIX + name: value for name, value in facts.items()}


def qualified_attack_runtime_dependencies(program):
    found = False
    version = False
    def visit(value):
        nonlocal found, version
        if isinstance(value, Mapping):
            field = value.get('field')
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
    return (QUALIFIED_ATTACK_CAPABILITY,) if found else ()
