from __future__ import annotations

"""Closed declaration of one resolution-time prevention recipient snapshot."""

from collections.abc import Mapping

RECIPIENT_SET_CAPABILITY = 'damage.prevention.fixed_recipient_set'
RECIPIENT_SET_KIND = 'current_creatures_and_players'


def validate_prevention_recipient_set(value):
    if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version'}
            or value.get('kind') != RECIPIENT_SET_KIND
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1):
        raise ValueError('Finite prevention recipient sets require their closed version-one selector')


def prevention_recipient_set_dependencies(value):
    if not isinstance(value, Mapping) or value.get('op') != 'create_damage_prevention_shield':
        return ()
    selector = value.get('selector')
    if not isinstance(selector, Mapping) or selector.get('kind') != RECIPIENT_SET_KIND:
        return ()
    validate_prevention_recipient_set(selector)
    if (set(value) != {'op', 'source', 'subject', 'mode', 'amount', 'duration', 'selector'}
            or not isinstance(value.get('source'), str) or not value['source']
            or value.get('subject') != '*' or value.get('mode') != 'amount'
            or type(value.get('amount')) is not int or value['amount'] < 1
            or value.get('duration') != 'until_end_of_turn'):
        raise ValueError('Recipient-set shields require a fixed positive amount and ordinary turn duration')
    return (RECIPIENT_SET_CAPABILITY,)
