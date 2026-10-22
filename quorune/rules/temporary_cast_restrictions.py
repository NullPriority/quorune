from __future__ import annotations

"""Resolution-owned player prohibitions with shared casting-legality queries."""

from typing import Mapping

from ..temporary_cast_rule_model import (CAPABILITY,MECHANIC,OPERATION,TemporaryCastRestrictionSpec,ResolvedCastRestriction)
from ..continuous_effect_state import commit_continuous_effect


def apply_temporary_cast_restriction(host,*,actor,restriction,target_player,source_id):
    host._require_seat(actor,in_game=True)
    if target_player is not None:host._require_seat(target_player,in_game=True)
    effect=ResolvedCastRestriction(host._next_ref('CR'),source_id,host._next_zone_timestamp(),actor,restriction,target_player)
    commit_continuous_effect(host.state,effect)
    host._log(actor,'casting.prohibition.created','A temporary casting restriction applies.',
        {'restriction':restriction.to_dict(),'target_player':target_player,'effect_id':effect.effect_id},importance=1)
    return effect.effect_id


def active_temporary_cast_restrictions(host,player):
    return tuple(effect for effect in (host.state.continuous_effects or ()) if isinstance(effect,ResolvedCastRestriction)
        and (effect.restriction.player_scope=='all'
             or effect.restriction.player_scope=='opponents' and player!=effect.controller
             or effect.restriction.player_scope=='target' and player==effect.target_player))


def restriction_matches_spell(effect,row):
    creature='creature' in row.types
    return effect.restriction.spell_kind=='any' or creature==(effect.restriction.spell_kind=='creature')


def temporary_cast_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if MECHANIC not in mechanic_ids or len(effects)!=1:return ()
    effect=effects[0]
    if set(effect)!={'op','actor','restriction','player'} or effect['op']!=OPERATION or effect['actor']!='$controller':return ()
    try:spec=TemporaryCastRestrictionSpec.from_dict(effect['restriction'])
    except (TypeError,ValueError):return ()
    if spec.player_scope=='target':
        if effect['player']!='$target.0' or target_schema not in (
                {'zones':['player'],'categories':['player'],'count':1,'player_relation':'any'},
                {'zones':['player'],'categories':['player'],'count':1,'player_relation':'opponent'}):return ()
        return tuple(sorted((CAPABILITY,'target.revalidate_resolution')))
    return (CAPABILITY,) if target_schema is None and effect['player'] is None else ()


def temporary_cast_runtime_dependencies(program):
    def walk(value):
        if isinstance(value,Mapping):
            if value.get('op')==OPERATION:
                spec=TemporaryCastRestrictionSpec.from_dict(value.get('restriction'))
                required=temporary_cast_node_capabilities(effects=(value,),target_schema=program.target_schema if spec.player_scope=='target' else None,mechanic_ids=program.coverage)
                if not required:raise ValueError('Temporary casting restriction lacks an owned player, spell subject or target')
                return set(required)
            return {cap for child in value.values() for cap in walk(child)}
        if isinstance(value,(tuple,list)):return {cap for child in value for cap in walk(child)}
        return set()
    return tuple(sorted(walk((program.effects,program.handlers))))
