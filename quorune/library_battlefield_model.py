from __future__ import annotations

"""Closed battlefield selection shape over existing fixed inspection policy."""

from collections.abc import Mapping
from copy import deepcopy

from .mana_value_limit import ManaValueLimitSpec

LIBRARY_BATTLEFIELD_CAPABILITY='library.select.battlefield_controller'
_ENTRY_FIELDS={'schema_version','selected_destination','entry_tapped','mana_value_limit'}


def fixed_selection_without_entry_fields(effect):
    return {key:deepcopy(value) for key,value in effect.items() if key not in _ENTRY_FIELDS}


def validate_library_battlefield_effect(effect):
    if (not isinstance(effect,Mapping) or type(effect.get('schema_version')) is not int
            or effect['schema_version']!=3 or effect.get('selected_destination')!='battlefield'
            or type(effect.get('entry_tapped')) is not bool or 'mana_value_limit' not in effect
            or effect.get('remainder_destination')!='library_bottom'):
        raise ValueError('Battlefield selection requires its closed entry and remainder shape')
    if effect['mana_value_limit'] is not None:ManaValueLimitSpec.from_dict(effect['mana_value_limit'])
    proxy=fixed_selection_without_entry_fields(effect);proxy['player']='$controller'
    from .rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
    if not fixed_library_selection_node_capabilities(effects=(proxy,),target_schema=None,
            mechanic_ids={'fixed-library-selection'}):
        raise ValueError('Battlefield selection lacks an owned fixed partition')
    # The initial entry family owns explicit creature, land and planeswalker
    # selections. Aura-capable/ambiguous sets need an attachment-choice owner.
    groups=effect['predicate_groups']
    if not groups or any(not _entry_query_is_owned(predicate) for group in groups for predicate in group):
        raise ValueError('Battlefield selection predicate requires an unrepresented entry quality')


def _entry_query_is_owned(value):
    from .object_predicate import ObjectQuerySpec
    query=ObjectQuerySpec.from_dict(value)
    kinds={'creature','land','planeswalker'}
    return bool(set(query.types_all)&kinds or query.types_any and set(query.types_any)<=kinds)


def library_mana_value_permits(effect,row):
    limit=effect.get('mana_value_limit')
    return limit is None or ManaValueLimitSpec.from_dict(limit).permits(row.mana_value)


def library_battlefield_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op')!='fixed_library_selection' or value.get('schema_version')!=3:return ()
    validate_library_battlefield_effect(value)
    return (LIBRARY_BATTLEFIELD_CAPABILITY,'library.select.fixed_controller',
        *(('zone.entry.tapped_state',) if value['entry_tapped'] else ()))
