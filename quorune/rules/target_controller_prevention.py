"""Bind a prevention source-controller predicate without changing its creator."""
import re
from collections.abc import Mapping
from ..damage_modifier_state import DamagePreventionScope


VERSION_FIELD='source_controller_target_version'
TARGET_FIELD='source_controller_target'
CAPABILITY='damage.prevention.target_player_sources'
MECHANIC='target-player-source-controller-prevention'
_FIELDS={'op','source','subject','mode','duration','damage_kind',VERSION_FIELD,TARGET_FIELD}


def player_target_schema(relation='opponent'):
    if relation not in {'any','opponent'}:raise ValueError('Prevention requires a player or opponent target')
    return {'zones':['player'],'categories':['player'],'count':1,'player_relation':relation}


def targeted_controller_prevention_effect(reference='$target.0'):
    return {'op':'create_damage_prevention_shield','source':'$source','subject':'*','mode':'all',
            'duration':'until_end_of_turn','damage_kind':'combat',VERSION_FIELD:1,TARGET_FIELD:reference}


def bound_prevention_player(effect,*,resolved=False):
    allowed=_FIELDS|({'_runtime_source','reason'} if resolved else set())
    if (not isinstance(effect,Mapping) or not _FIELDS<=set(effect)<=allowed
            or type(effect[VERSION_FIELD])is not int or effect[VERSION_FIELD]!=1
            or effect['op']!='create_damage_prevention_shield' or effect['subject']!='*'
            or effect['mode']!='all' or effect['duration']!='until_end_of_turn' or effect['damage_kind']!='combat'):
        raise ValueError('Target-controller combat prevention has a closed versioned instruction')
    source=effect['source'];player=effect[TARGET_FIELD]
    if type(source)is not str or not source or (source.startswith('$') if resolved else source!='$source'):
        raise ValueError('Target-controller prevention requires its typed spell source')
    if resolved and player is None:return None
    if (type(player)is not str or not player or (player.startswith('$') if resolved else
            re.fullmatch(r'\$target(?:\.[0-9]+|_group\.[A-Za-z0-9_:-]+\.[0-9]+)',player) is None)):
        raise ValueError('Target-controller prevention requires its bound player target')
    return player


def targeted_controller_prevention_dependencies(effect):
    if not isinstance(effect,Mapping) or not {VERSION_FIELD,TARGET_FIELD}.intersection(effect):return ()
    bound_prevention_player(effect)
    return (CAPABILITY,'damage.prevention.persistent_amount','target.revalidate_resolution')


def targeted_controller_prevention_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or MECHANIC not in mechanic_ids:return ()
    try:
        player=bound_prevention_player(effects[0])
        if (player!='$target.0' or not isinstance(target_schema,Mapping) or type(target_schema.get('count'))is not int
                or target_schema not in (player_target_schema('any'),player_target_schema('opponent'))):return ()
    except (TypeError,ValueError,KeyError):return ()
    return targeted_controller_prevention_dependencies(effects[0])


def targeted_prevention_player_unavailable(host,effect):
    player=bound_prevention_player(effect,resolved=True)
    if player is None:return True
    if player not in host.state.players:raise ValueError('Prevention source controller is an unknown player')
    return not host.state.players[player].in_game


def targeted_prevention_scope(effect):
    player=bound_prevention_player(effect,resolved=True)
    if player is None:raise ValueError('An unavailable prevention player cannot become an unrestricted scope')
    return DamagePreventionScope(source_controller=player,source_characteristics_all=('creature',))
