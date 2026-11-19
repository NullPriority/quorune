"""Targeted player authority, separate from the card chosen on resolution."""
from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..targeted_graveyard_exile_model import (
    GraveyardExileChoiceSpec, GRAVEYARD_EXILE_CHOICE_OPERATION as OPERATION,
    GRAVEYARD_EXILE_CHOICE_CAPABILITY as CAPABILITY,
)


DEPENDENCIES = (CAPABILITY, 'target.revalidate_resolution',
    'zone.change.destination_replacement', 'variant.commander.zone_return')


def _dependencies(spec):
    from .graveyard_exile_results import graveyard_exile_life_result_dependencies
    return (*DEPENDENCIES, *graveyard_exile_life_result_dependencies(spec.result))


def targeted_graveyard_exile_candidates(host, actor, effect):
    """Use the existing public-zone owner, retaining only physical cards."""
    from ..object_predicate import ObjectQuerySpec
    from ..public_zone_moves import (PublicZoneMoveSetSpec, PublicZoneOrigin, PublicZoneDestination,
        PublicZoneRelationAxis, PublicZoneSeatRelation, snapshot_public_zone_move_set)
    instruction = effect
    if isinstance(effect, Mapping) and '_choice_actor' in effect:
        if (set(effect) != {'op', 'player', 'selection', '_choice_actor', '_stack_label', '_graveyard_candidates'}
                or effect['_choice_actor'] != actor):
            raise ValueError('Graveyard exile candidate continuation fields and affected player are closed')
        instruction = {key: effect[key] for key in ('op', 'player', 'selection')}
    GraveyardExileChoiceSpec.from_effect(instruction, player=actor)
    if actor not in host.state.players:
        raise ValueError('Graveyard exile chooser must be a known player')
    if actor not in host.state.active_seats():
        return ()
    spec = PublicZoneMoveSetSpec(ObjectQuerySpec(zones=('graveyard',)), PublicZoneOrigin.GRAVEYARD,
        PublicZoneDestination.EXILE, relation_axis=PublicZoneRelationAxis.OWNER,
        seat_relation=PublicZoneSeatRelation.ACTOR, schema_version=2)
    snapshot = snapshot_public_zone_move_set(host, actor=actor, spec=spec)
    return tuple(sorted(value.ref for value in snapshot.objects if host.state.cards[value.object_id].is_card_object))


def graveyard_exile_player_target(*, opponent=False):
    if type(opponent) is not bool:
        raise ValueError('Targeted graveyard exile has a closed player relation')
    return {'zones': ['player'], 'categories': ['player'], 'count': 1,
        'player_relation': 'opponent' if opponent else 'any'}


def _target_is_closed(target):
    return isinstance(target, Mapping) and type(target.get('count')) is int and any(
        FrozenMap(target) == FrozenMap(graveyard_exile_player_target(opponent=opponent))
        for opponent in (False, True))


def targeted_graveyard_exile_node_capabilities(*, effects, target_schema, **kwargs):
    if len(effects) != 1 or not _target_is_closed(target_schema):
        return ()
    try:
        spec = GraveyardExileChoiceSpec.from_effect(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return _dependencies(spec)


def targeted_graveyard_exile_payload_dependencies(value):
    if not isinstance(value, Mapping) or value.get('op') != OPERATION:
        return ()
    return _dependencies(GraveyardExileChoiceSpec.from_effect(value))


def targeted_graveyard_exile_runtime_dependencies(program):
    def walk(value):
        if isinstance(value, Mapping):
            own = (value,) if value.get('op') == OPERATION else ()
            return own + tuple(node for child in value.values() for node in walk(child))
        if isinstance(value, (list, tuple)):
            return tuple(node for child in value for node in walk(child))
        return ()
    instructions = walk((program.effects, program.handlers))
    if not instructions:
        return ()
    if (len(instructions) != 1 or not _target_is_closed(program.target_schema)
            or not program.effects or program.effects[0] is not instructions[0]):
        raise ValueError('Graveyard exile choice requires its direct first instruction and one player target')
    return _dependencies(GraveyardExileChoiceSpec.from_effect(instructions[0]))


def is_closed_targeted_graveyard_exile_program(program):
    try:
        required = targeted_graveyard_exile_node_capabilities(
            effects=program.effects, target_schema=program.target_schema)
        handlers = tuple(getattr(program, 'handlers', ()))
        if handlers:
            from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
            validator = ActivatedAbilityCatalogHandler()
            for handler in handlers:
                validator.validate(handler)
        return bool(required) and set(required) <= set(program.capability_dependencies)
    except (TypeError, ValueError, AttributeError):
        return False
