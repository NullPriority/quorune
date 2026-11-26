from __future__ import annotations

"""Commit the existing activation payment and usage transaction together."""

from ...activation_usage import ActivationUsageError, activation_usage_verdict, commit_activation_usage
from ...errors import GameRuleError


def commit_activation_payments(host, proposal, source, ability, response, mana_option):
    from .commit import _commit_symbol_costs, _pay_object_and_mana_costs, _commit_activation_resource_costs

    from ..boast_allowance import maximum_boast_activations
    from ..whole_hand_mana import is_whole_hand_mana_ability

    maximum = maximum_boast_activations(host, proposal.seat, ability, source)
    _commit_symbol_costs(host, proposal, source, ability)
    if is_whole_hand_mana_ability(ability):
        from .whole_hand_mana_costs import commit_whole_hand_mana_cost

        commit_whole_hand_mana_cost(host, proposal.seat, source, ability, response)
        return [], [], {}, None
    if ability.mill_library_cost:
        from .mill_library_cost import pay_mill_library_activation_cost

        pay_mill_library_activation_cost(host, proposal.seat, ability, response)
    from ...mana_source_discovery import capture_hand_mana_window

    reserved = source if ability.discard_source or ability.exile_source else None
    with capture_hand_mana_window(host, proposal.seat, reserved_source=reserved):
        paid_objects, activations, spent, special_cost_context = _pay_object_and_mana_costs(
            host, proposal, source, ability, response,
        )
    _commit_activation_resource_costs(
        host,
        proposal,
        source,
        ability,
        response,
        mana_option,
    )
    if ability.source_counter_placement_cost is not None:
        from .counter_costs import pay_source_counter_placement

        pay_source_counter_placement(host, proposal, source, response)
    if ability.granting_attachment_payment is not None:
        from .granting_attachment import pay_granting_attachment
        equipment_id = pay_granting_attachment(host, actor=proposal.seat, source=source,
                                                payment=ability.granting_attachment_payment, response=response)
        paid_objects = [*paid_objects, equipment_id]
    try:
        commit_activation_usage(
            source,
            ability_id=ability.ability_id,
            limit=ability.activation_limit,
            turn_sequence=host.state.turn_sequence,
            maximum=maximum,
        )
        if maximum == 1 and ability.activation_limit is not None and activation_usage_verdict(
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
    from ..noted_mana import capture_noted_mana_payment

    special_cost_context = capture_noted_mana_payment(host.semantics.get(proposal.semantic_key), spent, special_cost_context)
    return paid_objects, activations, spent, special_cost_context


def announce_targeted_activation(host,proposal,source,ability,response,attachment_snapshot,source_context):
    """CR 602.2a: establish the ability before paying its source away.

    The ordinary command transaction rolls this frame back with any failed or
    suspended payment. Canonical departure capture can then refresh its source
    LKI during selected-object costs and mana abilities alike. Current games
    capture every nonmana ability; absent-version records keep targeted frames.
    """
    program=host.semantics.get(proposal.semantic_key)
    from ...ability_source_information import complete_ability_source_information
    if ability.mana_ability or not (ability.target_schema or getattr(program,'target_schema',None)
            or complete_ability_source_information(host.state)):return None
    from .commit import _activation_stack_item
    from ...target_source import capture_targeting_source, TARGETING_SOURCE_CONTEXT
    frame=_activation_stack_item(host,proposal,source,source.logical_object_id,ability,response,(),attachment_snapshot,None,
        {**dict(source_context or {}),TARGETING_SOURCE_CONTEXT:capture_targeting_source(host,source).to_dict()})
    host.state.stack.append(frame)
    return frame
