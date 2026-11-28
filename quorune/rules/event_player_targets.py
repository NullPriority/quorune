"""Bind a target controller or graveyard owner from the same closed occurrence."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..target_forms import EVENT_PLAYER_TARGET_REFERENCES
from .event_player_result_shapes import _event_player_reference_is_closed


DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY = 'zone.exile.damaged_player_graveyard_targets'
_DAMAGED_PLAYER_REFERENCE = '$context.target'


def damaged_player_graveyard_schema():
    from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec

    schema = PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CARD).to_target_schema()
    del schema['count']
    return {**schema, 'up_to': 2, 'owner_seat': _DAMAGED_PLAYER_REFERENCE}


def damaged_player_graveyard_effect():
    return {'op': 'exile_public_graveyard_targets', 'cards': '$targets', 'maximum_targets': 2}


def is_damaged_player_graveyard_exile(schema, effects):
    return (isinstance(schema, Mapping) and type(schema.get('up_to')) is int
            and thaw_value(schema) == damaged_player_graveyard_schema()
            and len(effects) == 1 and type(effects[0].get('maximum_targets')) is int
            and thaw_value(effects[0]) == damaged_player_graveyard_effect())


def is_source_combat_damage_player_binding(event, condition):
    from ..compiler.fixed_public_event_trigger_bindings import source_damage_incarnation_condition

    if event != 'damage.dealt.self' or not isinstance(condition, Mapping):
        return False
    expected = FrozenMap({'all': [
        {'field': 'target_kind', 'op': 'eq', 'value': 'player'},
        {'field': 'combat', 'op': 'truthy', 'value': True},
        source_damage_incarnation_condition(),
    ]})
    return FrozenMap(condition) == expected and condition['all'][1]['value'] is True


def event_player_direct_target_schema(schema, effects):
    """Validate the optional artifact role with its independently required source result."""
    if not isinstance(schema, Mapping) or 'up_to' not in schema:
        return schema
    fields = {'zones', 'categories', 'types_any', 'up_to'}
    if (type(schema['up_to']) is not int or schema['up_to'] != 1
            or set(schema) not in (fields | {'controller_relation'}, fields | {'controller_seat'})
            or tuple(schema['types_any']) != ('artifact',)
            or tuple(effects) != (
                {'op': 'destroy_targets', 'cards': '$targets', 'maximum_targets': 1},
                {'op': 'place_counters', 'card': '$source.zone_object', 'counter': '+1/+1',
                 'amount': 1, 'source': '$source'},
            ) or type(effects[0]['maximum_targets']) is not int or type(effects[1]['amount']) is not int):
        raise ValueError('An optional event-player target requires its exact artifact and source counter sequence')
    direct = dict(schema)
    del direct['up_to']
    direct['count'] = 1
    return direct


def bound_event_player_controller_schema(schema, context):
    if not isinstance(schema, Mapping):
        return schema
    from .combat_damage_library_access import uses_combat_damage_recipient_modes

    if uses_combat_damage_recipient_modes(schema):
        modes = {
            key: {**mode, "groups": [bound_event_player_controller_schema(group, context) for group in mode["groups"]]}
            for key, mode in schema["modes"].items()
        }
        return {**schema, "modes": modes}
    from .targeted_graveyard_outcomes import DAMAGE_TARGET_BOUND_FIELD, bind_damage_graveyard_target_bound

    if DAMAGE_TARGET_BOUND_FIELD in schema:
        schema = bind_damage_graveyard_target_bound(schema, context)
    for field in ('controller_seat', 'owner_seat'):
        reference = schema.get(field)
        if reference not in EVENT_PLAYER_TARGET_REFERENCES:
            continue
        player = context.get(reference.removeprefix('$context.'))
        if type(player) is not str or not player or player.startswith('$'):
            raise ValueError('Event-player target binding is unavailable')
        return {**schema, field: player}
    return schema


def is_graveyard_type_opponent_upkeep_binding(event, condition):
    """Recognize the ordinary opponent-upkeep schedule with a typed intervening fact."""
    from .scheduled_player_shapes import is_upkeep_participant_schedule

    if event != 'step.begin' or not isinstance(condition, Mapping) or set(condition) != {'all'}:
        return False
    clauses = thaw_value(condition)['all']
    if not isinstance(clauses, list) or len(clauses) != 2:
        return False
    schedule, predicate = clauses
    if (not is_upkeep_participant_schedule(schedule) or len(schedule['all']) != 2
            or not isinstance(predicate, Mapping) or predicate.get('value') is not True
            or set(predicate) != {'field', 'op', 'value', 'condition'}
            or predicate['field'] != FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD
            or predicate['op'] != 'truthy'):
        return False
    try:
        fact = FixedPublicStateConditionSpec.from_dict(predicate['condition'])
    except (TypeError, ValueError):
        return False
    return (fact.kind is FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST
            and fact.fact is FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_CARD_TYPES)


def _opponent_upkeep_creature_tap_dependencies(program, schema):
    from .scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY

    expected = {'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1,
                'types_any': ['creature'], 'controller_seat': '$context.player'}
    required_coverage = {CURRENT_ABILITY_FRAGMENT_COVERAGE, 'intervening_condition',
                         FIXED_PUBLIC_STATE_INTERVENING_COVERAGE}
    if (program.active_zone != 'battlefield' or thaw_value(schema) != expected
            or type(schema['count']) is not int or program.handlers or program.cost_schema is not None
            or program.trigger_limit is not None or program.destination is not None
            or not required_coverage.issubset(program.coverage)
            or not is_graveyard_type_opponent_upkeep_binding(program.event, program.event_condition)
            or tuple(program.effects) != ({'op': 'tap', 'card': '$target.0'},)):
        raise ValueError('An upkeep-player creature tap requires its exact opponent schedule and graveyard condition')
    return (SCHEDULED_PLAYER_CAPABILITY, 'trigger.condition.fixed_public_state',
            'permanent.tap.effect', 'target.revalidate_resolution')


def event_player_target_runtime_dependencies(program):
    schema = program.target_schema
    from .targeted_graveyard_outcomes import (
        DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY, DAMAGE_TARGET_BOUND_FIELD, targeted_graveyard_outcome_runtime_dependencies,
    )

    if (DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY in getattr(program, 'capability_dependencies', ())
            or isinstance(schema, Mapping) and DAMAGE_TARGET_BOUND_FIELD in schema):
        required = targeted_graveyard_outcome_runtime_dependencies(program)
        if DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY not in required:
            raise ValueError('Damage-linked graveyard targets require their complete committed outcome')
        return required
    if (DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY in getattr(program, "capability_dependencies", ())
            or isinstance(schema, Mapping) and 'owner_seat' in schema):
        if (program.active_zone != 'battlefield'
                or not is_source_combat_damage_player_binding(program.event, program.event_condition)
                or not is_damaged_player_graveyard_exile(schema, program.effects)):
            raise ValueError('Damaged-player graveyard targets require the original source combat damage and bounded exile')
        return (DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY, 'target.revalidate_resolution')
    if not isinstance(schema, Mapping) or schema.get('controller_seat') not in EVENT_PLAYER_TARGET_REFERENCES:
        return ()
    if program.event == 'step.begin' and schema['controller_seat'] == '$context.player':
        return _opponent_upkeep_creature_tap_dependencies(program, schema)
    from .provoke_shapes import provoke_program_runtime_dependencies
    provoke = provoke_program_runtime_dependencies(program)
    if provoke:
        return provoke
    from ..compiler.direct_target import DirectPermanentTargetSpec
    direct = event_player_direct_target_schema(schema, program.effects)
    DirectPermanentTargetSpec.from_target_schema(direct)
    if direct is not schema and (
        schema['controller_seat'] != '$context.target'
        or not is_source_combat_damage_player_binding(program.event, program.event_condition)
    ):
        raise ValueError('Optional artifact targets require current source combat damage to a player')
    if program.active_zone != 'battlefield' or not _event_player_reference_is_closed(program, schema['controller_seat']):
        raise ValueError('Event-player target lacks its closed occurrence scope')
    return ('target.revalidate_resolution',)
