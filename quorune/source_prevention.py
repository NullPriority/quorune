from __future__ import annotations

"""Closed all-damage shields on the original battlefield source incarnation."""

from collections.abc import Mapping

from .errors import GameRuleError

SOURCE_SUBJECT_VERSION = 'source_subject_version'
_FIELDS = {'op', 'source', 'subject', 'mode', 'duration', SOURCE_SUBJECT_VERSION}


def _source_prevention_shape_is_closed(value, *, runtime=False):
    fields = set(value) - {'_runtime_source'} if runtime else set(value)
    version = value.get(SOURCE_SUBJECT_VERSION)
    if type(version) is not int:
        return False
    if version == 1:
        return fields == _FIELDS
    return version == 2 and fields == _FIELDS | {'damage_kind'} and value.get('damage_kind') == 'combat'


def source_prevention_dependencies(value):
    if not isinstance(value, Mapping) or SOURCE_SUBJECT_VERSION not in value:
        return ()
    if (not _source_prevention_shape_is_closed(value) or value.get('op') != 'create_damage_prevention_shield'
            or value.get('source') != '$source' or value.get('subject') != '$source.zone_object'
            or value.get('mode') != 'all' or value.get('duration') != 'until_end_of_turn'):
        raise ValueError('Source-incarnation prevention has a closed versioned schema')
    return ('damage.prevention.persistent_amount',)


def source_prevention_subject_unavailable(host, effect, *, actor):
    if SOURCE_SUBJECT_VERSION not in effect:
        return False
    if (not _source_prevention_shape_is_closed(effect, runtime=True)
            or effect.get('op') != 'create_damage_prevention_shield'
            or type(effect.get('source')) is not str or not effect['source']
            or effect.get('mode') != 'all' or effect.get('duration') != 'until_end_of_turn'):
        raise GameRuleError('Source-incarnation prevention is malformed')
    subject = effect.get('subject')
    if subject is None:
        return True
    if type(subject) is not str or not subject:
        raise GameRuleError('A source prevention subject requires a permanent reference')
    from .damage import DamageError, recipient_snapshot
    try:
        snapshot = recipient_snapshot(host, subject, actor=actor)
    except DamageError:
        return True
    if snapshot.kind != 'permanent':
        raise GameRuleError('A source prevention subject must be a permanent')
    return False
