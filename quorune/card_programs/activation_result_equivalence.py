from __future__ import annotations

"""Comparison of complete fixed activation results through their typed owners."""

from ..compiler.hand_entry_templates import FixedPrivateHandEntryTemplate
from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import (
    PublicZoneMoveSetSpec, PublicZoneOrigin, PublicZoneDestination,
    PublicZoneRelationAxis, PublicZoneSeatRelation,
)
from ..rules.public_zone_move_capability_shapes import (
    fixed_private_hand_entry_node_capabilities, fixed_public_zone_move_set_node_capabilities,
)
from ..rules.reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
from ..rules.node_capability_shapes import fixed_counter_placement_target_set_node_capabilities
from .activation_target_equivalence import _fixed_graveyard_artifact_target, _optional_tapped_creature_target


def _owned(program, validator):
    required = validator(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _artifact_from_hand(current, old):
    if current.target_schema is not None or old.target_schema is not None:return False
    expected = FixedPrivateHandEntryTemplate(ObjectQuerySpec(zones=('hand',), types_all=('artifact',)))
    return (list(old.effects) == [{'op': 'put_artifact_from_hand', 'player': '$controller'}]
        and list(current.effects) == list(expected.effects)
        and _owned(current, fixed_private_hand_entry_node_capabilities))


def _graveyard_return(current, old):
    if _fixed_graveyard_artifact_target(current.target_schema) is None:return False
    if len(old.effects) != 1 or len(current.effects) != 1:return False
    effect = dict(old.effects[0])
    if 'reason' in effect:
        if type(effect.pop('reason')) is not str:return False
    if effect != {'op': 'move', 'card': '$target.0', 'destination': 'battlefield', 'controller': '$controller'}:return False
    return (dict(current.effects[0]) == {'op': 'reanimate', 'card': '$target.0',
        'controller': '$controller', 'tapped': False}
        and _owned(current, fixed_target_reanimation_node_capabilities))


def _opponent_graveyards(current, old):
    if current.target_schema is not None or old.target_schema is not None:return False
    if list(old.effects) != [{'op': 'exile_opponent_graveyards'}] or len(current.effects) != 1:return False
    effect = current.effects[0]
    if set(effect) != {'op', 'source', 'set'} or effect['op'] != 'move_public_zone_set' or effect['source'] != '$source':return False
    expected = PublicZoneMoveSetSpec(query=ObjectQuerySpec(zones=('graveyard',)),
        origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.EXILE,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.OPPONENTS)
    try:actual = PublicZoneMoveSetSpec.from_dict(effect['set'])
    except (TypeError, ValueError, KeyError):return False
    return actual == expected and _owned(current, fixed_public_zone_move_set_node_capabilities)


def _fixed_target_counter(current, old):
    if _optional_tapped_creature_target(current.target_schema) is None:return False
    if len(current.effects) != 1 or len(old.effects) != 1:return False
    a, b = (dict(p.effects[0]) for p in (current, old))
    if 'reason' in b:
        if type(b.pop('reason')) is not str:return False
    if (set(a) != {'op', 'cards', 'maximum_targets', 'counter', 'amount', 'source'}
            or a['op'] != 'place_counters_on_targets' or a['cards'] != '$targets'
            or a['source'] != '$source' or type(a['maximum_targets']) is not int or a['maximum_targets'] != 1
            or type(a['counter']) is not str or not a['counter'] or type(a['amount']) is not int or a['amount'] <= 0):return False
    expected = {'op': 'add_counter_selected', 'cards': '$targets', 'counter': a['counter'], 'amount': a['amount']}
    required = {'counter.producer.fixed_permanent_target_set_effect', 'target.revalidate_resolution'}
    return (type(b.get('amount')) is int and type(b.get('counter')) is str
        and b == expected and required <= set(current.capability_dependencies)
        and _owned(current, fixed_counter_placement_target_set_node_capabilities))


def fixed_activation_results_equal(current, old):
    return (_artifact_from_hand(current, old) or _graveyard_return(current, old)
        or _opponent_graveyards(current, old) or _fixed_target_counter(current, old))
