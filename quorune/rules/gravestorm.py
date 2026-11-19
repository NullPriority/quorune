"""Intrinsic Gravestorm count over the existing complete permanent-death journal."""
from collections.abc import Mapping

from ..public_history_query_model import PublicHistoryQuerySpec, public_history_query_value
from ..replacement.immutable import FrozenMap

GRAVESTORM_CAPABILITY = 'trigger.keyword.gravestorm'
GRAVESTORM_FIELD = 'gravestorm'
GRAVESTORM_DEPENDENCIES = (
    GRAVESTORM_CAPABILITY, 'stack.copy.cast_event_spell',
    'zone.turn_history.permanent_death', 'condition.history.fixed_public_query',
    'trigger.event.normalized_spell_cast', 'trigger.effect.fixed_event', 'trigger.placement.apnap',
)


def gravestorm_copy_count(state, controller):
    if type(controller) is not str or not controller or controller not in state.players:
        raise ValueError('Gravestorm requires its current known controller')
    # These two existing closed predicates partition all permanent deaths,
    # including noncreatures, tokens, and occurrences owned by departed seats.
    counts = tuple(public_history_query_value(state, controller,
        PublicHistoryQuerySpec('permanent_died', 'any_player', token=token, schema_version=3))
        for token in (False, True))
    if any(value is None for value in counts):
        raise ValueError('Gravestorm requires complete current-turn permanent-death history')
    return sum(counts)


def gravestorm_instruction():
    return {'op': 'choose_objects', 'player': '$controller',
        GRAVESTORM_FIELD: {'schema_version': 1}, 'stack': '$context.stack',
        'copy_source': '$context.event_spell_copy_source'}


def gravestorm_condition():
    return {'all': [{'field': 'controller', 'op': 'eq', 'value': '$source.controller'},
        {'field': 'card', 'op': 'eq', 'value': '$source.ref'}]}


def gravestorm_payload_dependencies(effect):
    if not isinstance(effect, Mapping) or GRAVESTORM_FIELD not in effect:
        return ()
    marker = effect[GRAVESTORM_FIELD]
    if (set(effect) != set(gravestorm_instruction())
            or not isinstance(marker, Mapping) or set(marker) != {'schema_version'}
            or type(marker['schema_version']) is not int or marker['schema_version'] != 1
            or dict(effect) != gravestorm_instruction()):
        raise ValueError('Gravestorm requires one closed intrinsic cast-copy instruction')
    return GRAVESTORM_DEPENDENCIES


def gravestorm_program_dependencies(program):
    if not any(isinstance(effect, Mapping) and GRAVESTORM_FIELD in effect for effect in program.effects):
        return ()
    if (len(program.effects) != 1 or program.target_schema is not None
            or program.event != 'spell.cast' or program.active_zone != 'stack'
            or FrozenMap(program.event_condition or {}) != FrozenMap(gravestorm_condition())):
        raise ValueError('Intrinsic Gravestorm requires the actual source spell cast')
    return gravestorm_payload_dependencies(program.effects[0])


def gravestorm_node_capabilities(*, effects, target_schema, mechanic_ids=(), cost_schema=None):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        return gravestorm_payload_dependencies(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()


def decode_resolving_gravestorm(effect, *, actor, trigger_ref):
    from ..event_spell_copy_model import EventSpellCopySource
    marker = effect.get(GRAVESTORM_FIELD) if isinstance(effect, Mapping) else None
    if (not isinstance(effect, Mapping) or set(effect) != set(gravestorm_instruction())
            or effect.get('op') != 'choose_objects' or effect.get('player') != actor
            or not isinstance(marker, Mapping) or set(marker) != {'schema_version'}
            or type(marker['schema_version']) is not int or marker['schema_version'] != 1):
        raise ValueError('Gravestorm requires its exact resolving instruction and current actor')
    source = EventSpellCopySource.from_dict(effect['copy_source'])
    if (source.item().kind != 'spell' or source.item().ref != effect['stack']
            or source.item().ref == trigger_ref):
        raise ValueError('Gravestorm requires the actual original spell, separate from its trigger')
    return source


def bind_gravestorm_instruction(host, item, effect):
    if GRAVESTORM_FIELD not in effect:
        return effect
    decode_resolving_gravestorm(effect, actor=item.controller, trigger_ref=item.ref)
    # This read occurs as the trigger starts resolving. The delegated copy
    # target choices subsequently consume this fixed count without recounting.
    return {**dict(effect), '_gravestorm_copies': gravestorm_copy_count(host.state, item.controller)}


def capture_gravestorm_cast_source(host, source, event):
    if (source.zone != 'stack' or not isinstance(event, Mapping)
            or event.get('card') != source.ref or event.get('controller') != source.controller):
        raise ValueError('Gravestorm requires its actual source spell cast')
    item = next((item for item in host.state.stack if item.ref == event.get('stack')), None)
    if (item is None or item.kind != 'spell' or item.card_object_id != source.object_id
            or item.controller != source.controller
            or event.get('logical_object_id') != source.logical_object_id):
        raise ValueError('Gravestorm cast source changed its spell object or incarnation')
    from .event_spell_copy import capture_event_spell_copy_source
    return capture_event_spell_copy_source(host, item)
