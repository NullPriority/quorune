"""Resolution-created next-spell rules using the shared journal and cost query."""
from __future__ import annotations

from .next_spell_cost_model import ResolvedNextSpellCostReduction,validate_next_spell_cost_instruction
from .cast_cost_modifiers import CastCostAffectedController,PublicCastCostModifierSpec
from .object_query import object_matches_query,object_query_result


def apply_next_spell_cost_reduction(host,effect,*,actor,reason,operation=None):
    from .continuous_effect_state import commit_continuous_effect,resolution_effect_source
    public={key:value for key,value in effect.items() if key!='_runtime_source'}
    query,_=validate_next_spell_cost_instruction(public,resolved=True)
    host._require_seat(actor,in_game=True)
    source=resolution_effect_source(host,effect)
    pending=ResolvedNextSpellCostReduction(host._next_ref('NC'),source.stack_ref,
        host._next_zone_timestamp(),actor,query,public['amount'])
    commit_continuous_effect(host.state,pending)
    host._log(actor,'casting.next_cost.created','A next-spell cost reduction applies.',
        {'effect_id':pending.effect_id,'amount':pending.amount,'spell_query':query.to_dict()},importance=1)
    return pending.effect_id


def matching_next_spell_cost_reductions(host,seat,card,cast_type_line=None,face_down_method=None):
    pending=tuple(effect for effect in host.state.continuous_effects or ()
        if isinstance(effect,ResolvedNextSpellCostReduction) and effect.controller==seat)
    if not pending:return ()
    from .compiled_cast_timing import prospective_conditional_flash_spell_data
    data=dict(prospective_conditional_flash_spell_data(host,card,seat,face_down_method=face_down_method))
    if cast_type_line is not None:data['type_line']=cast_type_line
    row=object_query_result(card,data,type_parts=host._type_parts(str(data.get('type_line') or '')),
        known_to_actor=True,attached_to_ref=None)
    return tuple(effect for effect in pending if object_matches_query(row,effect.spell_query))


def active_next_spell_cost_reductions(host,seat,card,cast_type_line=None,face_down_method=None):
    return tuple(PublicCastCostModifierSpec(CastCostAffectedController.SOURCE_CONTROLLER,
        (effect.spell_query,),-effect.amount)
        for effect in matching_next_spell_cost_reductions(host,seat,card,cast_type_line,face_down_method) if effect.amount)


def consume_next_spell_rules(host,proposal,card,*,preparing):
    """Preserve historical next-spell flags and consume only completed casts."""
    stats=host.state.players[proposal.seat].stats
    from .rules.next_spell_payment import capture_next_spell_payments
    used_improvise,next_payments=capture_next_spell_payments(stats,host.state.turn_sequence,preparing=preparing)
    read=stats.get if preparing else stats.pop
    used_uncounterable=bool(read('next_spell_uncounterable',False))
    if not preparing:
        from .continuous_effect_state import consume_continuous_effects
        matched=matching_next_spell_cost_reductions(host,proposal.seat,card,proposal.type_line)
        consume_continuous_effects(host.state,tuple(effect.effect_id for effect in matched))
    return used_improvise,used_uncounterable,next_payments
