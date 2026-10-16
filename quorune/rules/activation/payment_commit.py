from __future__ import annotations

"""Commit the existing activation payment and usage transaction together."""

from ...activation_usage import ActivationUsageError, activation_usage_verdict, commit_activation_usage
from ...errors import GameRuleError


def commit_activation_payments(host, proposal, source, ability, response, mana_option):
    from .commit import _commit_symbol_costs, _pay_object_and_mana_costs, _commit_activation_resource_costs

    _commit_symbol_costs(host, proposal, source, ability)
    paid_objects, activations, spent, special_cost_context = (
        _pay_object_and_mana_costs(
            host, proposal, source, ability, response
        )
    )
    _commit_activation_resource_costs(
        host,
        proposal,
        source,
        ability,
        response,
        mana_option,
    )
    try:
        commit_activation_usage(
            source,
            ability_id=ability.ability_id,
            limit=ability.activation_limit,
            turn_sequence=host.state.turn_sequence,
        )
        if ability.activation_limit is not None and activation_usage_verdict(
            source,
            ability_id=ability.ability_id,
            limit=ability.activation_limit,
            turn_sequence=host.state.turn_sequence,
        ).available:
            raise ActivationUsageError(
                "Activation usage commit did not consume the typed limit"
            )
    except ActivationUsageError as exc:
        raise GameRuleError(str(exc)) from exc
    return paid_objects, activations, spent, special_cost_context
