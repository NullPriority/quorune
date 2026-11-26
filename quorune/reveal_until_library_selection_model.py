from __future__ import annotations

"""A public prefix scan delegates its complete partition to existing owners."""

from collections.abc import Mapping
from copy import deepcopy

from .object_predicate import ObjectQuerySpec
from .object_query import object_matches_query
from .replacement.immutable import FrozenMap

REVEAL_UNTIL_OPERATION = 'reveal_until_library_selection'
REVEAL_UNTIL_MECHANIC = 'reveal-until-library-selection'
REVEAL_UNTIL_CAPABILITY = 'library.select.reveal_until_controller'
REVEAL_UNTIL_REFLEXIVE_VERSION = 2
REVEAL_UNTIL_REFLEXIVE_CAPABILITY = 'trigger.reflexive.revealed_creature_mana_value_counters'
PLAYER_CAPPED_LAND_REVEAL_VERSION = 3
PLAYER_CAPPED_LAND_REVEAL_CAPABILITY = 'library.select.player_capped_announced_land_reveal'
_FIELDS = {'op','schema_version','player','stop_count','predicate_groups','selection',
    'selected_destination','entry_tapped','remainder_destination','remainder_order'}


def player_capped_land_reveal_effect(*, player='$controller', stop_count='$x'):
    predicate = ObjectQuerySpec(zones=('library',), types_any=('land',))
    return {'op': REVEAL_UNTIL_OPERATION, 'schema_version': PLAYER_CAPPED_LAND_REVEAL_VERSION,
            'player': player, 'stop_count': stop_count, 'predicate_groups': [[predicate.to_dict()]],
            'selection': 'matching', 'selected_destination': 'battlefield', 'entry_tapped': True,
            'remainder_destination': 'library_bottom', 'remainder_order': 'random'}


def _valid_stop_count(effect):
    count = effect['stop_count']
    if effect['schema_version'] == PLAYER_CAPPED_LAND_REVEAL_VERSION:
        return count == '$x' or type(count) is int and count >= 0
    return type(count) is int and count > 0


def _fixed_selection(effect, count):
    all_revealed = effect['selection'] == 'all_revealed'
    fixed = {'op':'fixed_library_selection','player':effect['player'],'look_count':count,
        'public_reveal':True,'selected_reveal':True,
        'selection_policy':'fixed_any' if all_revealed else 'all_matching',
        'minimum':count if all_revealed else 0,'maximum':count,
        'predicate_groups':[] if all_revealed else deepcopy(effect['predicate_groups']),
        'remainder_destination':effect['remainder_destination'],'remainder_order':effect['remainder_order']}
    if effect['selected_destination'] == 'battlefield':
        fixed.update(schema_version=3,selected_destination='battlefield',
            entry_tapped=effect['entry_tapped'],mana_value_limit=None)
    return fixed


def validate_reveal_until_selection(effect):
    if (not isinstance(effect,Mapping) or set(effect)!=_FIELDS
            or effect.get('op')!=REVEAL_UNTIL_OPERATION
            or type(effect['schema_version']) is not int
            or effect['schema_version'] not in (1, REVEAL_UNTIL_REFLEXIVE_VERSION, PLAYER_CAPPED_LAND_REVEAL_VERSION)
            or type(effect['player']) is not str or not effect['player'] or effect['player'].strip()!=effect['player']
            or not _valid_stop_count(effect)
            or type(effect['selection']) is not str or effect['selection'] not in {'all_revealed','matching'}
            or type(effect['selected_destination']) is not str or effect['selected_destination'] not in {'hand','battlefield'}
            or type(effect['entry_tapped']) is not bool
            or effect['entry_tapped'] and effect['selected_destination']!='battlefield'
            or effect['selection']=='all_revealed' and (effect['selected_destination']!='hand'
                or effect['remainder_destination']!='graveyard' or effect['remainder_order']!='chosen')):
        raise ValueError('Reveal-until selection has a closed controller prefix schema')
    from .rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
    announced = effect['stop_count']
    probe = _fixed_selection(effect, max(1, announced) if type(announced) is int else 1)
    probe.update(player='$controller',selection_policy='all_matching',minimum=0,
        predicate_groups=deepcopy(effect['predicate_groups']))
    required = fixed_library_selection_node_capabilities(effects=(probe,),target_schema=None,
        mechanic_ids={'fixed-library-selection'})
    if not required:
        raise ValueError('Reveal-until stop and partition require the existing fixed selection owner')
    predicates = tuple(ObjectQuerySpec.from_dict(raw) for raw in effect['predicate_groups'][0])
    if effect['schema_version'] == REVEAL_UNTIL_REFLEXIVE_VERSION:
        if (effect['stop_count'] != 1 or predicates != (ObjectQuerySpec(zones=('library',), types_any=('creature',)),)
                or len(effect['predicate_groups']) != 1 or effect['selection'] != 'matching'
                or effect['selected_destination'] != 'hand'
                or (effect['remainder_destination'], effect['remainder_order']) != ('library_bottom', 'random')):
            raise ValueError('The reflexive counter result requires one creature stopping a random-bottom partition')
        from .rules.reveal_until_reflexive import reveal_until_reflexive_dependencies
        required = (*required, *reveal_until_reflexive_dependencies())
    if effect['schema_version'] == PLAYER_CAPPED_LAND_REVEAL_VERSION:
        expected = player_capped_land_reveal_effect(player=effect['player'], stop_count=announced)
        if FrozenMap(effect) != FrozenMap(expected):
            raise ValueError('Announced X requires the exact tapped-land and random-bottom partition')
        required = (*required, PLAYER_CAPPED_LAND_REVEAL_CAPABILITY)
    return predicates, required


def read_reveal_until_prefix(query, *, player, predicates, stop_count):
    """Return current card rows and their matches through the stopping card."""
    rows, matches = [], []
    seen = set()
    for ref in query.library_refs(player, top_first=True):
        row = query.object(ref,zones=('library',))
        if (row is None or row.ref!=ref or row.owner!=player or row.token
                or not row.logical_object_id or row.object_id in seen):
            raise ValueError('Reveal-until requires unique current owned physical library cards')
        seen.add(row.object_id)
        rows.append(row)
        if any(object_matches_query(row, predicate) for predicate in predicates):
            matches.append(ref)
        if len(matches) == stop_count:
            break
    return tuple(rows), tuple(matches)


def fixed_selection_after_reveal_until(effect, query):
    """Read only through the stopping match, without moving or publishing cards."""
    fixed, _ = reveal_until_partition(effect, query)
    return fixed


def reveal_until_partition(effect, query):
    """Read the exact prefix and its final matching occurrence."""
    predicates, _ = validate_reveal_until_selection(effect)
    stop_count = effect['stop_count']
    if type(stop_count) is not int:
        raise ValueError('Reveal-until requires the committed announced X')
    if stop_count == 0:
        return None, None
    rows, matches = read_reveal_until_prefix(query, player=effect['player'], predicates=predicates, stop_count=stop_count)
    stopping_match = rows[-1] if len(matches) == stop_count else None
    return (_fixed_selection(effect, len(rows)) if rows else None, stopping_match)


def reveal_until_selection_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op')!=REVEAL_UNTIL_OPERATION:return ()
    _, required = validate_reveal_until_selection(value)
    return (REVEAL_UNTIL_CAPABILITY,*required)
