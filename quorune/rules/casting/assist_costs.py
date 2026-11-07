from __future__ import annotations

"""Shared Assist affordability against one locked casting price."""

from ...cost_payments import cost_is_affordable
from .source_placement import cast_payment_read_host


ASSIST_HELPER_FIELD = "assist_helper"
ASSIST_OPTION_FIELD = "assist_payment_version"


def _minimum_generic_help(requirements, caster_affordable):
    generic = requirements["GENERIC"]
    if caster_affordable(requirements):
        return 0
    if generic == 0 or not caster_affordable({**requirements, "GENERIC": 0}):
        return None
    # Paying less generic never makes the same colored price less affordable.
    low, high = 1, generic
    while low < high:
        middle = (low + high) // 2
        if caster_affordable({**requirements, "GENERIC": generic - middle}):
            high = middle
        else:
            low = middle + 1
    return low


def select_assist_cost(
    host, seat, option, response, *, hint, choice_schema, spend_context, exclude_sources,
):
    requirements = option["requirements"]
    payer = response.get(ASSIST_HELPER_FIELD)
    read_host = cast_payment_read_host(host, seat)

    def caster_affordable(price):
        return cost_is_affordable(
            read_host, seat, price, exclude_sources=exclude_sources, spend_context=spend_context,
        )

    minimum = _minimum_generic_help(requirements, caster_affordable)
    if minimum is None:
        return False
    if not hint and payer is None:
        return minimum == 0
    decision = read_host.state.pending_decision
    if read_host.state.priority_player != seat or (
        decision is not None and decision.kind not in {"priority", "cast.assist"}
    ):
        return minimum == 0 and payer is None
    others = [player for player in read_host.active_seats if player != seat]
    if not hint and payer not in others:
        return False
    helpers = [
        player for player in others
        if requirements["GENERIC"] and (minimum == 0 or cost_is_affordable(
            read_host, player, {"GENERIC": minimum}, spend_context=spend_context,
        ))
    ]
    if hint:
        if helpers:
            choice_schema[ASSIST_HELPER_FIELD] = {
                "label": "Choose another player for Assist",
                "legal_values": helpers,
                "required": minimum > 0,
            }
        return minimum == 0 or bool(helpers)
    if payer not in helpers:
        return False
    option[ASSIST_HELPER_FIELD] = payer
    return True
