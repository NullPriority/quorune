from __future__ import annotations

"""A public prefix scan delegates its complete partition to existing owners."""

from collections.abc import Mapping
from copy import deepcopy

from .object_predicate import ObjectQuerySpec
from .object_query import object_matches_query

REVEAL_UNTIL_OPERATION = 'reveal_until_library_selection'
REVEAL_UNTIL_MECHANIC = 'reveal-until-library-selection'
REVEAL_UNTIL_CAPABILITY = 'library.select.reveal_until_controller'
_FIELDS = {'op','schema_version','player','stop_count','predicate_groups','selection',
    'selected_destination','entry_tapped','remainder_destination','remainder_order'}


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
            or type(effect['schema_version']) is not int or effect['schema_version']!=1
            or type(effect['player']) is not str or not effect['player'] or effect['player'].strip()!=effect['player']
            or type(effect['stop_count']) is not int or effect['stop_count']<=0
            or type(effect['selection']) is not str or effect['selection'] not in {'all_revealed','matching'}
            or type(effect['selected_destination']) is not str or effect['selected_destination'] not in {'hand','battlefield'}
            or type(effect['entry_tapped']) is not bool
            or effect['entry_tapped'] and effect['selected_destination']!='battlefield'
            or effect['selection']=='all_revealed' and (effect['selected_destination']!='hand'
                or effect['remainder_destination']!='graveyard' or effect['remainder_order']!='chosen')):
        raise ValueError('Reveal-until selection has a closed controller prefix schema')
    from .rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
    probe = _fixed_selection(effect, max(1,effect['stop_count']))
    probe.update(player='$controller',selection_policy='all_matching',minimum=0,
        predicate_groups=deepcopy(effect['predicate_groups']))
    required = fixed_library_selection_node_capabilities(effects=(probe,),target_schema=None,
        mechanic_ids={'fixed-library-selection'})
    if not required:
        raise ValueError('Reveal-until stop and partition require the existing fixed selection owner')
    return tuple(ObjectQuerySpec.from_dict(raw) for raw in effect['predicate_groups'][0]), required


def fixed_selection_after_reveal_until(effect, query):
    """Read only through the stopping match, without moving or publishing cards."""
    predicates, _ = validate_reveal_until_selection(effect)
    matches = count = 0
    seen = set()
    for ref in query.library_refs(effect['player'],top_first=True):
        row = query.object(ref,zones=('library',))
        if (row is None or row.ref!=ref or row.owner!=effect['player'] or row.token
                or not row.logical_object_id or row.object_id in seen):
            raise ValueError('Reveal-until requires unique current owned physical library cards')
        seen.add(row.object_id)
        count += 1
        if any(object_matches_query(row,predicate) for predicate in predicates):matches += 1
        if matches == effect['stop_count']:break
    return _fixed_selection(effect,count) if count else None


def reveal_until_selection_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op')!=REVEAL_UNTIL_OPERATION:return ()
    _, required = validate_reveal_until_selection(value)
    return (REVEAL_UNTIL_CAPABILITY,*required)
