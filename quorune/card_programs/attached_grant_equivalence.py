from __future__ import annotations

"""Source-bound precedence for equivalent pure attached grant providers."""

from typing import Any, Iterable
from collections.abc import Mapping
from dataclasses import replace

from ..ability_fragments import (
    GrantedActivatedAbilitySpec, GrantedTriggeredAbilitySpec,
    ability_fragment_from_dict, ability_fragment_to_dict,
)
from ..object_predicate import ObjectQuerySpec
from ..util import stable_json
from ..targets import target_plan


_EMPTY_MODIFIER = {
    'type_operations': [], 'color_operations': [], 'add_abilities': [],
    'remove_abilities': [], 'remove_all_abilities': False, 'add_rules_text': [],
    'power': 0, 'toughness': 0, 'base_power': None, 'base_toughness': None,
    'quantity': None, 'quantity_power': 0, 'quantity_toughness': 0,
}


def _grant_view(program: Any, *, generated: bool):
    from ..semantic_runtime.attached_continuous import AttachedFixedCharacteristicsHandler
    from ..semantic_runtime.composite_grants import CompositeAbilityGrantHandler

    handlers = [row for row in program.handlers
        if row.get('handler_id') != 'activation.catalog.pinned.v1']
    if len(handlers) != 1 or program.effects:
        return None
    descriptor = handlers[0]
    try:
        if generated:
            CompositeAbilityGrantHandler().validate(descriptor)
            target = descriptor['target']
            if (target['kind'] != 'attached' or target['controller'] is not None
                    or target['exclude_source'] is not False
                    or ObjectQuerySpec.from_dict(target['predicate']) != ObjectQuerySpec(
                        zones=('battlefield',), types_all=('creature',))):
                return None
        else:
            AttachedFixedCharacteristicsHandler().validate(descriptor)
            if descriptor['condition'] != {'relation': 'source_attached_object'}:
                return None
        modifier = descriptor['modifier']
        if set(modifier) - set(_EMPTY_MODIFIER) - {'add_ability_fragments'}:
            return None
        if any(modifier.get(key, default) != default for key, default in _EMPTY_MODIFIER.items()):
            return None
        views = []
        references = []
        fragments = []
        for value in modifier.get('add_ability_fragments', ()):
            fragment = ability_fragment_from_dict(value)
            if not isinstance(fragment, (GrantedActivatedAbilitySpec, GrantedTriggeredAbilitySpec)) or fragment.schema_version != 1:
                return None
            row = ability_fragment_to_dict(fragment)
            body = dict(row['value'])
            references.append(body.pop('semantic_key'))
            body.pop('ability_id')
            for key in ('effect_text', 'label'):
                if key in body:
                    body[key] = body[key].strip().rstrip('.')
            views.append(stable_json({'kind': row['kind'], 'value': body}))
            fragments.append(fragment)
        return (tuple(sorted(views)), tuple(references), tuple(views), tuple(fragments)) if views else None
    except (KeyError, TypeError, ValueError):
        return None


def reviewed_attached_grant_equivalent(candidate: Any, reviewed: Any) -> bool:
    """Match one current pure provider to its exact reviewed source view."""
    if (candidate.trust_level != 'trusted' or reviewed.trust_level != 'trusted'
            or not candidate.oracle_id or candidate.oracle_id != reviewed.oracle_id
            or not str(candidate.provenance.get('authored_by', '')).startswith('oracle-ir-v')
            or str(reviewed.provenance.get('authored_by', '')).startswith('oracle-ir-v')
            or candidate.provenance.get('granted_only') is True
            or reviewed.provenance.get('granted_only') is True
            or not candidate.capability_closure or candidate.capability_closure.get('trusted') is not True):
        return False
    if any(getattr(candidate, field) != getattr(reviewed, field) for field in (
            'active_zone', 'event', 'event_condition', 'cost_schema', 'target_schema',
            'destination', 'requires_arbiter')):
        return False
    if any(not candidate.provenance.get(field)
            or candidate.provenance[field] != reviewed.provenance.get(field)
            for field in ('source_oracle_hash', 'source_rulings_hash')):
        return False
    current = _grant_view(candidate, generated=True)
    old = _grant_view(reviewed, generated=False)
    return current is not None and old is not None and current[0] == old[0]


def _targets_match(current, old):
    if current == old:
        return True
    if current is None or old is None:
        return False
    try:
        a, b = target_plan(current), target_plan(old)
        if any(plan.modes or plan.globally_distinct or plan.same_player_groups
                or len(plan.groups) != 1 for plan in (a, b)):
            return False
        if any(group.min_targets != 1 or group.max_targets != 1 for group in (*a.groups, *b.groups)):
            return False
        return replace(a.groups[0], group_id='target') == replace(b.groups[0], group_id='target')
    except (TypeError, ValueError, KeyError):
        return False


def _fixed_cost_matches_fragment(cost, fragment):
    if cost is None:
        return True
    defaults = {'complex_symbols': [], 'untap_source': False, 'discard_source': False,
        'sacrifice_source': False, 'exile_source': False, 'life_payment': 0,
        'energy_payment': 0, 'loyalty_delta': None, 'source_counter_removal_cost': None,
        'choices': [], 'uncompiled_costs': []}
    if (not isinstance(fragment, GrantedActivatedAbilitySpec) or not isinstance(cost, Mapping)
            or set(cost) - set(defaults) - {'text', 'mana', 'tap_source'}
            or cost.get('text') != fragment.cost_text
            or cost.get('tap_source') is not fragment.tap_source
            or any(cost.get(key, default) != default for key, default in defaults.items())):
        return False
    mana = cost.get('mana')
    return (isinstance(mana, Mapping)
        and all(type(value) is int and value >= 0 for value in mana.values())
        and {key: value for key, value in mana.items() if value} == dict(fragment.mana))


def _death_condition(value, event):
    if event != 'creature.dies' or value is None:
        return value
    if value in ({'field': 'types', 'op': 'contains_any', 'value': ['creature']},
                 {'field': 'types', 'op': 'contains_all', 'value': ['creature']}):
        return None
    if (value == {'field': 'token', 'op': 'in', 'value': [False, True]}
            and all(type(item) is bool for item in value['value'])):
        return None
    return value


def _child_matches(current, old, fragment, parent):
    if old.trust_level != 'trusted' or current.oracle_id != old.oracle_id:
        return False
    if any(not parent.provenance.get(field) or any(program.provenance.get(field) != parent.provenance[field]
            for program in (current, old)) for field in ('source_oracle_hash', 'source_rulings_hash')):
        return False
    if current.event != 'activate' and old.provenance.get('granted_only') is not True:
        return False
    metadata = {'key', 'label', 'ability_id', 'provenance', 'tests', 'coverage',
        'trust_level', 'capability_dependencies', 'capability_closure',
        'effects', 'target_schema', 'cost_schema', 'event_condition'}
    if ({key: value for key, value in current.to_dict().items() if key not in metadata}
            != {key: value for key, value in old.to_dict().items() if key not in metadata}):
        return False
    if not _targets_match(current.target_schema, old.target_schema):
        return False
    if isinstance(fragment, GrantedActivatedAbilitySpec):
        if not all(_fixed_cost_matches_fragment(program.cost_schema, fragment) for program in (current, old)):
            return False
    elif current.cost_schema != old.cost_schema:
        return False
    if _death_condition(current.event_condition, current.event) != _death_condition(old.event_condition, old.event):
        return False
    if current.effects == old.effects:
        return True
    # The printed self-untap uses the receiver's original incarnation. Upgrade
    # only this complete legacy body to the existing typed zone-object value.
    return (isinstance(fragment, GrantedTriggeredAbilitySpec) and current.event == 'creature.dies'
        and list(old.effects) == [{'op': 'untap', 'card': '$source'}]
        and list(current.effects) == [{'op': 'untap', 'card': '$source.zone_object'}])


def _children_match(current, old, current_programs, old_programs, parent):
    remaining = list(zip(current[2], current[1], current[3]))
    for view, key in zip(old[2], old[1]):
        if key not in old_programs:
            return False
        match = next((index for index, (candidate_view, candidate_key, fragment) in enumerate(remaining)
            if candidate_view == view and _child_matches(current_programs[candidate_key],
                old_programs[key], fragment, parent)), None)
        if match is None:
            return False
        remaining.pop(match)
    return not remaining


def shadowed_reviewed_attached_grant_keys(record: Any, generated: Iterable[Any], reviewed: Iterable[Any]) -> set[str]:
    """Replace a provider only with its complete trusted child graph present."""
    programs = tuple(generated)
    reviewed = tuple(reviewed)
    by_key = {program.key: program for program in programs}
    old_by_key = {program.key: program for program in reviewed}
    result = set()
    for candidate in programs:
        if candidate.oracle_id != record.oracle_id:
            continue
        current = _grant_view(candidate, generated=True)
        if current is None or any(key not in by_key or by_key[key].trust_level != 'trusted'
                or by_key[key].provenance.get('granted_only') is not True
                or not by_key[key].capability_closure
                or by_key[key].capability_closure.get('trusted') is not True for key in current[1]):
            continue
        for old in reviewed:
            if reviewed_attached_grant_equivalent(candidate, old) and _children_match(
                    current, _grant_view(old, generated=False), by_key, old_by_key, candidate):
                result.add(old.key)
    return result
