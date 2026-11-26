from __future__ import annotations

from typing import Mapping

from ...errors import GameRuleError
from ...milling import MillRequest, commit_mill, prepare_mill
from ..activation_zone_change_costs import counted_activation_replacement_selections


MILL_LIBRARY_ACTIVATION_COST_CAPABILITY = "activation.library_mill.fixed_cost"
MILL_LIBRARY_COST_REFS_FIELD = "_mill_library_cost_refs"


def _prepared_cost(host, actor, count):
    if type(count) is not int or count != 1:
        raise GameRuleError("The activation requires one card from your library")
    plan = prepare_mill(host, MillRequest(actor=actor, player=actor, count=count, reason="activated ability cost"))
    if len(plan.top_first) != count:
        raise GameRuleError("The library does not contain enough cards for the Mill cost")
    return plan


def pay_mill_library_activation_cost(host, actor, ability, response):
    plan = _prepared_cost(host, actor, ability.mill_library_cost)
    refs = tuple(identity.ref for identity in plan.top_first)
    sealed = response.get(MILL_LIBRARY_COST_REFS_FIELD)
    if sealed is not None and (not isinstance(sealed, (list, tuple)) or tuple(sealed) != refs):
        raise GameRuleError("The original top card changed during the Mill cost")
    if response.get("cost_cards") or response.get("cost_objects"):
        raise GameRuleError("A fixed Mill cost does not select an object")
    selections = counted_activation_replacement_selections(response, refs)
    commit_mill(host, plan, replacement_selections=selections)


def freeze_mill_library_cost_response(host, actor, response):
    summary = response.get("cost_summary")
    if not isinstance(summary, Mapping) or "mill_library" not in summary:
        return response
    from ...abilities import choose_ability
    from .model import ActivationProposalRequest

    request = ActivationProposalRequest.from_submission(actor, response)
    source = host._resolve_object(actor, request.source_ref, zones=set(request.zones), controlled_only=True)
    ability = choose_ability(host._activated_abilities(source), request.ability_selector)
    if summary["mill_library"] != ability.mill_library_cost:
        raise GameRuleError("The activated Mill cost changed during the continuation")
    plan = _prepared_cost(host, actor, ability.mill_library_cost)
    return {**response, MILL_LIBRARY_COST_REFS_FIELD: [identity.ref for identity in plan.top_first]}
