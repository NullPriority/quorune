"""Use the shared chosen X and selected zone-cost transaction for a subtype price."""

from ...activation_x import chosen_activation_x
from ..announced_subtype_sacrifice import announced_subtype_sacrifice_choice
from ...errors import GameRuleError
from ..activation_costs import activation_choice_candidates, pay_fixed_zone_change_cost


def selected_subtype_sacrifice_refs(response, announced_x):
    if "cost_objects" in response and "cost_cards" in response:
        raise ValueError("Submit one selection for the announced subtype sacrifice")
    refs = response.get("cost_objects", response.get("cost_cards", ()))
    if (type(announced_x) is not int or announced_x < 0 or not isinstance(refs, (list, tuple))
            or len(refs) != announced_x or any(type(ref) is not str or not ref for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ValueError("Sacrifice exactly the announced number of distinct permanents")
    return tuple(refs)


def announced_subtype_sacrifice_proposal_details(host, actor, source, ability, announced_x, response):
    choice = announced_subtype_sacrifice_choice(ability)
    if choice is None:
        return {}
    from .model import ActivationProposalError

    try:
        refs = selected_subtype_sacrifice_refs(response, announced_x)
        legal = activation_choice_candidates(host, actor, source, choice, ability=ability)
        if not set(refs).issubset(legal):
            raise ValueError("A selected sacrifice is stale, prohibited, or outside the controlled subtype")
    except ValueError as exc:
        raise ActivationProposalError(str(exc), reason="invalid_announced_subtype_sacrifice") from exc
    return {"announced_subtype_sacrifice_refs": refs}


def pay_announced_subtype_sacrifices(host, actor, ability, choice, response):
    try:
        announced_x = chosen_activation_x(ability, response)
        refs = selected_subtype_sacrifice_refs(response, announced_x)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return pay_fixed_zone_change_cost(
        host, actor=actor, cost=choice.fixed_zone_change_cost(), refs=refs,
        response=response, reason="activated ability cost", expected_count=announced_x,
    )


def variable_object_activation_offer(host, actor, ability, hint, x_schema):
    if ability.variable_colored_reveal_price is not None:
        from .variable_colored_reveal import variable_colored_reveal_offer

        return variable_colored_reveal_offer(host, actor, ability.variable_colored_reveal_price, x_schema)
    if announced_subtype_sacrifice_choice(ability) is None:
        return {}
    refs = hint["choose_cost"][0]["legal_refs"]
    return {
        "x": {**x_schema, "maximum": len(refs)},
        "cost_objects": {
            "type": "object_ref_array", "legal_refs": refs, "zone": "battlefield",
            "minimum": 0, "maximum": len(refs), "distinct": True, "payment": "sacrifice",
            "count_from": "x", "label": "Sacrifice X matching permanents you control",
        },
    }
