from __future__ import annotations

"""Automatic exact random hand costs using the canonical discard owners."""

from typing import Any,Mapping

from ...abilities import CostChoice,choose_ability
from ...errors import GameRuleError
from ...object_predicate import ObjectQuerySpec
from ...random_discard import RandomDiscardIntent,resolve_random_discard
from ...replacement.immutable import FrozenMap
from ..activation_costs import pay_counted_zone_change_activation_cost
from .model import ActivationProposalRequest


def random_discard_cost_refs(host,actor,source,ability,response) -> tuple[str,...]:
    """Bind one exact random subset to the server's stable payment identity."""
    count=ability.random_discard_payment
    if not count or not ability.compiled_cost:
        raise GameRuleError('Random-discard activation cost is not represented')
    if response.get('cost_cards') or response.get('cost_objects'):
        raise GameRuleError('Random discard is not a selected card cost')
    payment_id=response.get('_mana_payment_id')
    if type(payment_id) is not str or not payment_id:
        raise GameRuleError('Random discard requires its server payment identity')
    if len(host.state.players[actor].zones['hand'])<count:
        raise GameRuleError('Random discard requires its full hand-card count')
    intent=RandomDiscardIntent(actor=actor,players=(actor,),count=count,
        randomization_key=f'{payment_id}|activation-random-discard|{source.logical_object_id}|{ability.ability_id}',
        reason='activated ability cost')
    selected=tuple(resolve_random_discard(host,intent,commit_batch=lambda refs:refs))
    if len(selected)!=count or len(set(selected))!=count:
        raise GameRuleError('Random discard did not select the exact distinct cost')
    frozen=response.get('_random_discard_cost_refs')
    if frozen is not None and (not isinstance(frozen,(list,tuple)) or tuple(frozen)!=selected):
        raise GameRuleError('Random discard selection changed across payment continuation')
    return selected


def pay_random_discard_activation_cost(host,actor,source,ability,response) -> list[str]:
    selected=random_discard_cost_refs(host,actor,source,ability,response)
    choice=CostChoice(kind='discard_one',count=ability.random_discard_payment,zone='hand',
        predicate=FrozenMap(ObjectQuerySpec(zones=('hand',),owner='$actor',known_to_actor=True).to_dict()))
    return pay_counted_zone_change_activation_cost(host,actor=actor,source=source,choice=choice,
        response={**response,'cost_cards':selected})


def freeze_random_discard_cost_response(host,actor,response:Mapping[str,Any]) -> Mapping[str,Any]:
    """Seal private selected refs only when a rolled-back cost must suspend."""
    summary=response.get('cost_summary')
    if not isinstance(summary,Mapping) or 'random_discard' not in summary:
        return response
    request=ActivationProposalRequest.from_submission(actor,response)
    source=host._resolve_object(actor,request.source_ref,zones=set(request.zones),controlled_only=True)
    try:ability=choose_ability(host._activated_abilities(source),request.ability_selector)
    except ValueError as exc:raise GameRuleError(str(exc)) from exc
    if type(summary['random_discard']) is not int or summary['random_discard']!=ability.random_discard_payment:
        raise GameRuleError('Random discard continuation cost identity changed')
    selected=random_discard_cost_refs(host,actor,source,ability,response)
    return {**response,'_random_discard_cost_refs':list(selected)}
