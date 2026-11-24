from __future__ import annotations

"""Closed all-damage shields on the original battlefield source incarnation."""

from collections.abc import Mapping

from .damage_prevention_scope import DamagePreventionScope
from .errors import GameRuleError
from .replacement.immutable import FrozenMap


SOURCE_SUBJECT_VERSION = 'source_subject_version'
SOURCE_COMBAT_DIRECTION = 'source_combat_direction'
SOURCE_COMBAT_PREVENTION_CAPABILITY = 'damage.prevention.source_block_combat'
EXCLUDED_SOURCE_TARGET_VERSION = "excluded_source_target_version"
EXCLUDED_SOURCE_TARGET = "excluded_source_target"
EXCLUDED_SOURCE_TARGET_FIELDS = frozenset((EXCLUDED_SOURCE_TARGET_VERSION, EXCLUDED_SOURCE_TARGET))
_EXCLUDED_SOURCE_TARGET_SCHEMAS = tuple(
    FrozenMap({"zones": ["battlefield"], "categories": ["permanent"], "count": 1, role: ["creature"]})
    for role in ("types_any", "types_all")
)
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


def targeted_source_exclusion_schema_is_closed(schema):
    return (
        isinstance(schema, Mapping) and type(schema.get("count")) is int
        and FrozenMap(schema) in _EXCLUDED_SOURCE_TARGET_SCHEMAS
    )


def targeted_source_exclusion_effect():
    return {
        "op": "create_damage_prevention_shield", "source": "$source", "subject": "*",
        "mode": "all", "duration": "until_end_of_turn", "damage_kind": "combat",
        EXCLUDED_SOURCE_TARGET_VERSION: 1, EXCLUDED_SOURCE_TARGET: "$target.0",
    }


def materialize_targeted_source_exclusion(host, effect, *, actor):
    if not EXCLUDED_SOURCE_TARGET_FIELDS.intersection(effect):
        return effect
    target = effect.get(EXCLUDED_SOURCE_TARGET)
    source = effect.get("source")
    if (type(target) is not str or not target or target.startswith("$")
            or type(source) is not str or not source or source.startswith("$")):
        raise GameRuleError("Combat prevention requires its resolved source and original creature target")
    expected = {**targeted_source_exclusion_effect(), "source": source, EXCLUDED_SOURCE_TARGET: target}
    if ({key: value for key, value in effect.items() if key != "_runtime_source"} != expected
            or type(effect.get(EXCLUDED_SOURCE_TARGET_VERSION)) is not int):
        raise GameRuleError("Target-excluding combat prevention has a closed versioned schema")
    from .damage import recipient_snapshot

    original = recipient_snapshot(host, target, actor=actor)
    if original.kind != "permanent" or "creature" not in original.types or original.logical_object_id is None:
        raise GameRuleError("Combat prevention requires a current creature target")
    scope = DamagePreventionScope(
        source_characteristics_all=("creature",), excluded_source_logical_object_id=original.logical_object_id,
    )
    return {**{key: value for key, value in effect.items() if key not in EXCLUDED_SOURCE_TARGET_FIELDS},
            "scope": scope.to_dict()}


def source_prevention_dependencies(value):
    if not isinstance(value, Mapping):
        return ()
    if EXCLUDED_SOURCE_TARGET_FIELDS.intersection(value):
        if (value != targeted_source_exclusion_effect()
                or type(value.get(EXCLUDED_SOURCE_TARGET_VERSION)) is not int):
            raise ValueError("Target-excluding combat prevention has a closed versioned schema")
        return ("damage.prevention.persistent_amount", "target.revalidate_resolution")
    if SOURCE_SUBJECT_VERSION not in value:
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
    target_exclusions = []
    def visit(value):
        if isinstance(value,Mapping):
            if EXCLUDED_SOURCE_TARGET_FIELDS.intersection(value):
                required.update(source_prevention_dependencies(value))
                target_exclusions.append(value)
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
    if target_exclusions and (
        (program.event, program.active_zone) != ("resolve", "stack")
        or not targeted_source_exclusion_schema_is_closed(program.target_schema)
        or tuple(program.effects) != tuple(target_exclusions) or len(target_exclusions) != 1
        or program.cost_schema is not None or program.handlers
    ):
        raise ValueError("Target-excluding combat prevention requires the whole original single-creature spell")
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
