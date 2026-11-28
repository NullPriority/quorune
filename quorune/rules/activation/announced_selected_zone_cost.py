"""Use the shared chosen X and selected zone-cost transaction for variable object costs."""

from ...activation_x import chosen_activation_x
from ..announced_hand_discard import ANNOUNCED_HAND_DISCARD_KIND, announced_hand_discard_choice
from ..announced_subtype_sacrifice import announced_subtype_sacrifice_choice
from ...errors import GameRuleError
from ..activation_costs import activation_choice_candidates, pay_fixed_zone_change_cost


def announced_selected_zone_cost_choice(ability):
    return announced_subtype_sacrifice_choice(ability) or announced_hand_discard_choice(ability)


def selected_announced_cost_refs(response, announced_x):
    if "cost_objects" in response and "cost_cards" in response:
        raise ValueError("Submit one selection for the announced object cost")
    refs = response.get("cost_objects", response.get("cost_cards", ()))
    if (type(announced_x) is not int or announced_x < 0 or not isinstance(refs, (list, tuple))
            or len(refs) != announced_x or any(type(ref) is not str or not ref for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ValueError("Select exactly the announced number of distinct cost objects")
    return tuple(refs)


def announced_selected_zone_cost_details(host, actor, source, ability, announced_x, response):
    choice = announced_selected_zone_cost_choice(ability)
    if choice is None:
        return {}
    from .model import ActivationProposalError

    try:
        refs = selected_announced_cost_refs(response, announced_x)
        legal = activation_choice_candidates(host, actor, source, choice, ability=ability)
        if not set(refs).issubset(legal):
            raise ValueError("A selected payment object is stale or ineligible")
    except ValueError as exc:
        reason = ("invalid_announced_hand_discard" if choice.kind == ANNOUNCED_HAND_DISCARD_KIND
                  else "invalid_announced_subtype_sacrifice")
        raise ActivationProposalError(str(exc), reason=reason) from exc
    field = ("announced_hand_discard_refs" if choice.kind == ANNOUNCED_HAND_DISCARD_KIND
             else "announced_subtype_sacrifice_refs")
    return {field: refs}


def pay_announced_selected_zone_cost(host, actor, ability, choice, response):
    try:
        announced_x = chosen_activation_x(ability, response)
        refs = selected_announced_cost_refs(response, announced_x)
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
    choice = announced_selected_zone_cost_choice(ability)
    if choice is None:
        return {}
    refs = hint["choose_cost"][0]["legal_refs"]
    discard = choice.kind == ANNOUNCED_HAND_DISCARD_KIND
    return {
        "x": {**x_schema, "maximum": len(refs)},
        "cost_objects": {
            "type": "object_ref_array", "legal_refs": refs, "zone": choice.zone,
            "minimum": 0, "maximum": len(refs), "distinct": True,
            "payment": "discard" if discard else "sacrifice", "count_from": "x",
            "label": "Discard X cards from your hand" if discard else "Sacrifice X matching permanents you control",
        },
    }
