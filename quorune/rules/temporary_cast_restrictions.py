from __future__ import annotations

"""Resolution-owned player casting rules with shared legality and cost queries."""

from typing import Mapping

from ..temporary_cast_rule_model import (
    CAPABILITY, MECHANIC, NEXT_TURN_NONHAND_CAPABILITY, OPERATION, TemporaryCastRestrictionSpec, ResolvedCastRestriction,
)
from ..continuous_effect_state import commit_continuous_effect
from ..next_spell_cost_model import MECHANIC as NEXT_COST_MECHANIC, OPERATION as NEXT_COST_OPERATION, next_spell_cost_node_capabilities, validate_next_spell_cost_instruction


def apply_temporary_cast_restriction(host,*,actor,restriction,target_player,source_id):
    host._require_seat(actor,in_game=True)
    if target_player is not None:host._require_seat(target_player,in_game=True)
    effect=ResolvedCastRestriction(host._next_ref('CR'),source_id,host._next_zone_timestamp(),actor,restriction,
                                   target_player,restriction.duration)
    commit_continuous_effect(host.state,effect)
    host._log(actor,'casting.prohibition.created','A temporary casting restriction applies.',
        {'restriction':restriction.to_dict(),'target_player':target_player,'effect_id':effect.effect_id},importance=1)
    return effect.effect_id


def active_temporary_cast_restrictions(host,player):
    return tuple(effect for effect in (host.state.continuous_effects or ()) if isinstance(effect,ResolvedCastRestriction)
        and (effect.restriction.player_scope=='all'
             or effect.restriction.player_scope=='opponents' and player!=effect.controller
             or effect.restriction.player_scope=='target' and player==effect.target_player))


def restriction_matches_spell(effect,row,*,origin):
    if effect.restriction.nonhand_until_next_turn:
        return origin != "hand"
    creature='creature' in row.types
    return effect.restriction.spell_kind=='any' or creature==(effect.restriction.spell_kind=='creature')


def temporary_cast_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if NEXT_COST_MECHANIC in mechanic_ids:
        return next_spell_cost_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if MECHANIC not in mechanic_ids or len(effects)!=1:return ()
    effect=effects[0]
    if set(effect)!={'op','actor','restriction','player'} or effect['op']!=OPERATION or effect['actor']!='$controller':return ()
    try:spec=TemporaryCastRestrictionSpec.from_dict(effect['restriction'])
    except (TypeError,ValueError):return ()
    if spec.nonhand_until_next_turn:
        return (CAPABILITY, NEXT_TURN_NONHAND_CAPABILITY) if target_schema is None and effect['player'] is None else ()
    if spec.player_scope=='target':
        if effect['player'] == '$context.defending_player':
            if (target_schema is None and spec.spell_kind == 'any'
                    and 'trigger-event-normalized-self-attack' in mechanic_ids):
                return (CAPABILITY, 'trigger.event.normalized_self_attack', 'trigger.placement.apnap')
            return ()
        if effect['player']!='$target.0' or target_schema not in (
                {'zones':['player'],'categories':['player'],'count':1,'player_relation':'any'},
                {'zones':['player'],'categories':['player'],'count':1,'player_relation':'opponent'}):return ()
        return tuple(sorted((CAPABILITY,'target.revalidate_resolution')))
    return (CAPABILITY,) if target_schema is None and effect['player'] is None else ()


def temporary_cast_runtime_dependencies(program):
    from .stack_counter_followup import STACK_COUNTER_FOLLOWUP_OPERATION, stack_counter_followup_runtime_dependencies

    def walk(value):
        if isinstance(value,Mapping):
            if value.get('op') == STACK_COUNTER_FOLLOWUP_OPERATION:
                return {CAPABILITY} & set(stack_counter_followup_runtime_dependencies(program))
            if value.get('op') == NEXT_COST_OPERATION:
                if value.get('schema_version') == 2 and (
                    (program.event, program.active_zone) != ('creature.attacks', 'battlefield')
                    or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
                    or program.target_schema is not None or getattr(program, 'cost_schema', None) is not None
                    or len(program.effects) != 1 or value is not program.effects[0] or program.handlers
                    or 'trigger-event-normalized-self-attack' not in program.coverage or NEXT_COST_MECHANIC not in program.coverage
                ):
                    raise ValueError('Recurring spell costs require one exact source attack result')
                return set(validate_next_spell_cost_instruction(value)[1])
            if value.get('op')==OPERATION:
                spec=TemporaryCastRestrictionSpec.from_dict(value.get('restriction'))
                if value.get('player') == '$context.defending_player' and (
                    (program.event, program.active_zone) != ('creature.attacks', 'battlefield')
                    or program.event_condition != {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
                    or program.target_schema is not None or getattr(program, 'cost_schema', None) is not None
                    or len(program.effects) != 1 or value is not program.effects[0] or program.handlers
                ):
                    raise ValueError('Casting prohibition requires one sealed source attack without targets')
                required=temporary_cast_node_capabilities(effects=(value,),target_schema=program.target_schema if spec.player_scope=='target' else None,mechanic_ids=program.coverage)
                if not required:raise ValueError('Temporary casting restriction lacks an owned player, spell subject or target')
                return set(required)
            return {cap for child in value.values() for cap in walk(child)}
        if isinstance(value,(tuple,list)):return {cap for child in value for cap in walk(child)}
        return set()
    return tuple(sorted(walk((program.effects,program.handlers))))



def attacked_defender_cast_recipient_has_left(host, raw_effect, effect):
    """A resolved attack occurrence can outlive its defending player."""
    recipient = effect.get('player')
    return (raw_effect.get('player') == '$context.defending_player'
        and isinstance(recipient, str) and recipient in host.state.players
        and not host.state.players[recipient].in_game)
