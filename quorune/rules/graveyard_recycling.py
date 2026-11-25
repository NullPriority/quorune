"""Recycle public graveyards through canonical movement and shuffle ports."""
from collections.abc import Mapping
from ..graveyard_recycling_model import (
    GraveyardRecyclingSpec, GRAVEYARD_RECYCLING_OPERATION, GRAVEYARD_RECYCLING_CAPABILITY,
    HAND_GRAVEYARD_RECYCLING_CAPABILITY,
)

_PLAYER_SCHEMA = {'zones': ['player'], 'categories': ['player'], 'player_relation': 'any', 'count': 1}


def _target_schema_matches(schema, scope):
    if scope == 'target_player':
        return isinstance(schema, Mapping) and type(schema.get('count')) is int and dict(schema) == _PLAYER_SCHEMA
    return schema is None or isinstance(schema, Mapping) and not schema


def _compiled_recycling_dependencies(value):
    if (not isinstance(value, Mapping) or set(value) != {'op', 'recycling', 'player'}
            or value.get('op') != GRAVEYARD_RECYCLING_OPERATION):
        raise ValueError('Compiled recycling fields are incomplete or unknown')
    spec = GraveyardRecyclingSpec.from_dict(value['recycling'])
    player = {'controller': '$controller', 'target_player': '$target.0', 'all_players': 'all'}[spec.scope]
    if value['player'] != player:
        raise ValueError('Compiled recycling recipient differs from its declared scope')
    return spec, (GRAVEYARD_RECYCLING_CAPABILITY, 'zone.change.destination_replacement',
        *(('target.revalidate_resolution',) if spec.scope == 'target_player' else ()),
        *((HAND_GRAVEYARD_RECYCLING_CAPABILITY,) if spec.include_hand else ()))


def graveyard_recycling_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) not in (1, 2):
        return ()
    try:
        spec, dependencies = _compiled_recycling_dependencies(effects[-1])
    except (TypeError, ValueError):
        return ()
    if (not _target_schema_matches(target_schema, spec.scope)
            or 'fixed-owner-zone-move' not in mechanic_ids
            or spec.scope == 'target_player' and 'cr-115-targets' not in mechanic_ids):
        return ()
    if spec.include_source:
        if (len(effects) != 2 or target_schema is not None or 'cr-119-life' not in mechanic_ids
                or effects[0] != {"op": "life", "player": "$controller", "delta": 5}
                or type(effects[0]["delta"]) is not int):
            return ()
        return (*dependencies, "life.change.effect")
    return dependencies if len(effects) == 1 else ()


def source_graveyard_recycling_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 2:
        return ()
    return graveyard_recycling_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
    )


def is_closed_graveyard_recycling_program(program):
    required = graveyard_recycling_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def graveyard_recycling_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if value.get('op') == GRAVEYARD_RECYCLING_OPERATION:
                spec, dependencies = _compiled_recycling_dependencies(value)
                if not _target_schema_matches(program.target_schema, spec.scope):
                    raise ValueError('Recycling requires its exact complete player-target declaration')
                if spec.include_source:
                    dependencies = graveyard_recycling_node_capabilities(
                        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
                    )
                    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

                    if (not dependencies or (program.event, program.active_zone) != ("activate", "battlefield")
                            or len(program.handlers) != 1
                            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID
                            or program.event_condition is not None or program.cost_schema is None):
                        raise ValueError("Source recycling requires the original activation and both ordered results")
                required.update(dependencies)
            for child in value.values(): visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value: visit(child)
    visit((program.effects, program.handlers))
    return tuple(sorted(required))


def _recycling_players(host, spec, player, actor):
    active = tuple(host.public_zone_move_active_seats())
    if actor not in active:
        raise ValueError('Graveyard recycling requires the actual active resolving controller')
    if spec.scope == 'all_players':
        if player != 'all': raise ValueError('All-player recycling has no individual player operand')
        order = tuple(host.public_zone_move_apnap_order())
        if len(order) != len(set(order)) or set(order) != set(active):
            raise ValueError('Graveyard recycling requires the complete current participant order')
        return order
    if type(player) is not str or player not in active:
        raise ValueError('Graveyard recycling player is unavailable')
    if spec.scope == 'controller' and player != actor:
        raise ValueError('Controller recycling cannot redirect its graveyard')
    return (player,)


def _present_recycling_source(host, source_context):
    card = host.state.cards.get(source_context.object_id)
    if (card is not None and card.zone == "battlefield" and not card.phased_out
            and card.logical_object_id == source_context.logical_object_id):
        return card
    return None


def resolve_graveyard_recycling(host, effect, *, actor, reason, source_context=None):
    if (not isinstance(effect, Mapping) or effect.get('op') != GRAVEYARD_RECYCLING_OPERATION
            or set(effect) - {'op', 'recycling', 'player', 'reason', '_runtime_source', '_replacement_selections'}
            or not {'op', 'recycling', 'player'} <= set(effect)):
        raise ValueError('Graveyard recycling instruction fields are incomplete or unknown')
    spec = GraveyardRecyclingSpec.from_dict(effect['recycling'])
    players = _recycling_players(host, spec, effect['player'], actor)
    if spec.include_source:
        from ..semantic_runtime.context import SemanticSourceContext

        if not isinstance(source_context, SemanticSourceContext):
            raise ValueError("Source recycling requires its original activation frame")
    elif source_context is not None:
        raise ValueError("Historical recycling cannot carry a source object")
    references = []
    seen = set()
    for player in players:
        for origin in spec.origins:
            for object_id in tuple(host.state.players[player].zones[origin]):
                card = host.state.cards.get(object_id)
                if (card is None or card.zone != origin or card.owner != player or object_id in seen):
                    raise ValueError('Whole collection membership changed before recycling')
                seen.add(object_id); references.append(card.ref)
    shuffle_players = set(players)
    origins = spec.origins
    if spec.include_source:
        original = _present_recycling_source(host, source_context)
        if original is not None:
            if original.object_id in seen or original.owner not in host.public_zone_move_active_seats():
                raise ValueError("The original recycling source lost its distinct owner or incarnation")
            references.append(original.ref)
            origins = (*origins, "battlefield")
            shuffle_players.add(original.owner)
    selections = tuple(effect.get('_replacement_selections') or ())
    if not references and selections:
        raise ValueError('Empty graveyards have no per-object replacement selections')
    moved = ()
    if references:
        from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
        # The canonical replacement exception suspends before any move. No
        # shuffle occurs until all destination choices have actually committed.
        moved = host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=tuple(references), expected_zones=origins,
            destination='library', reason=reason, replacement_selections=selections))
    if spec.include_source:
        from ..zone_transitions import ZoneTransitionOwner

        zone_owner = ZoneTransitionOwner(host)
        for player in host.public_zone_move_apnap_order():
            if player in shuffle_players:
                zone_owner.shuffle_library(player, reason=reason, actor=actor)
    else:
        for player in players:
            host.shuffle_library(player, reason=reason)
    return moved


def execute_graveyard_recycling(host, intent):
    return resolve_graveyard_recycling(host, {
        'op': GRAVEYARD_RECYCLING_OPERATION, 'recycling': intent.recycling.to_dict(),
        'player': intent.player, '_replacement_selections': intent.replacement_selections,
    }, actor=intent.actor, reason=intent.reason, source_context=intent.source_context)
