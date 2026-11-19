from __future__ import annotations

"""Closed all-damage shields on the original battlefield source incarnation."""

from collections.abc import Mapping

from .errors import GameRuleError

SOURCE_SUBJECT_VERSION = 'source_subject_version'
SOURCE_COMBAT_DIRECTION = 'source_combat_direction'
SOURCE_COMBAT_PREVENTION_CAPABILITY = 'damage.prevention.source_block_combat'
_FIELDS = {'op', 'source', 'subject', 'mode', 'duration', SOURCE_SUBJECT_VERSION}


def _source_prevention_shape_is_closed(value, *, runtime=False):
    fields = set(value) - {'_runtime_source'} if runtime else set(value)
    version = value.get(SOURCE_SUBJECT_VERSION)
    if type(version) is not int:
        return False
    if version == 1:
        return fields == _FIELDS
    if version == 3:
        return fields == _FIELDS | {'damage_kind',SOURCE_COMBAT_DIRECTION,'application_group_id'} and value.get('damage_kind')=='combat' and value.get(SOURCE_COMBAT_DIRECTION) in ('to','from') and type(value.get('application_group_id')) is str and bool(value['application_group_id'])
    return version == 2 and fields == _FIELDS | {'damage_kind'} and value.get('damage_kind') == 'combat'


def source_prevention_dependencies(value):
    if not isinstance(value, Mapping) or SOURCE_SUBJECT_VERSION not in value:
        return ()
    if (not _source_prevention_shape_is_closed(value) or value.get('op') != 'create_damage_prevention_shield'
            or value.get('source') != '$source' or value.get('subject') != '$source.zone_object'
            or value.get('mode') != 'all' or value.get('duration') != 'until_end_of_turn'):
        raise ValueError('Source-incarnation prevention has a closed versioned schema')
    if value[SOURCE_SUBJECT_VERSION]==3 and value['application_group_id']!='$stack':
        raise ValueError('Source combat shields require their original resolving application group')
    return ('damage.prevention.persistent_amount', * ((SOURCE_COMBAT_PREVENTION_CAPABILITY,) if value[SOURCE_SUBJECT_VERSION]==3 else ()))


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


def source_block_combat_runtime_dependencies(program):
    required=set()
    marked=[]
    def visit(value):
        if isinstance(value,Mapping):
            if value.get(SOURCE_SUBJECT_VERSION)==3 or SOURCE_COMBAT_DIRECTION in value:
                required.update(source_prevention_dependencies(value));marked.append(value)
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    if marked and (program.event not in ('creature.blocks','creature.becomes_blocked')
            or program.active_zone!='battlefield' or program.target_schema is not None
            or dict(program.event_condition or {})!={'field':'card','op':'eq','value':'$source.ref'}):
        raise ValueError('Self combat prevention requires the original source block occurrence')
    if marked:required.update(('trigger.event.normalized_public_action','trigger.placement.apnap'))
    return tuple(sorted(required))


def materialize_source_combat_prevention(effect):
    if effect.get(SOURCE_SUBJECT_VERSION)!=3:return effect
    if not _source_prevention_shape_is_closed(effect,runtime=True):
        raise GameRuleError('Source combat prevention direction is malformed')
    if effect[SOURCE_COMBAT_DIRECTION]=='to':return effect
    from .damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES
    from .object_predicate import ObjectQuerySpec
    return {**effect,'subject':'*','chosen_source':effect['subject'],
        'source_predicate':ObjectQuerySpec(zones=REPRESENTED_DAMAGE_SOURCE_ZONES,known_to_actor=True).to_dict()}
