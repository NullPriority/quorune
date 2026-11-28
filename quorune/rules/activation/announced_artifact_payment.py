"""Shared public offer and selected-payment validation for announced artifact costs."""

from itertools import combinations

from ...errors import GameRuleError
from ...mana_restrictions import ability_mana_spend_context
from ...target_source import ability_targeting_source
from ..activation_costs import activation_choice_candidates, prepare_selected_zone_change_cost
from ..announced_artifact_sacrifice_return import announced_power_return_schema
from .x_costs import activation_requirements


def _spend_context(host, source):
    return ability_mana_spend_context(
        str(host._effective_card_data(source).get("type_line") or ""), source_zone=source.zone,
    )


def _maximum_payable_count(host, seat, viable_sources, artifact_ids, requirements, spend_context):
    choice_sources = tuple(source.object_id for source in viable_sources if source.object_id in artifact_ids)
    # Only artifacts that could produce mana need to be left out of the sacrifice.
    for required_sources in range(len(choice_sources) + 1):
        for retained in combinations(choice_sources, required_sources):
            if host._cost_is_affordable(
                seat, requirements, exclude_sources=artifact_ids.difference(retained), spend_context=spend_context,
            ):
                return len(artifact_ids) - required_sources
    return 0


def artifact_sacrifice_x_offer(host, seat, source, ability):
    choice, = ability.choices
    artifact_refs = activation_choice_candidates(host, seat, source, choice, ability=ability)
    if not artifact_refs:
        return None
    artifact_ids = {host._target_snapshot(ref)["object_id"] for ref in artifact_refs}
    spend_context = _spend_context(host, source)
    mana_sources = tuple(host.available_mana_sources(seat, spend_context=spend_context))
    acting = ability_targeting_source(host, source)
    views = {}

    def target_view(maximum):
        if maximum not in views:
            views[maximum] = host._public_target_schema(
                seat, announced_power_return_schema(maximum), acting_source=acting,
                source_ref=source.ref, announcing_targets=True,
            )
        return views[maximum] or {}

    public = target_view(len(artifact_ids))
    bounds = {}
    max_by_requirements = {}
    for ref in public.get("legal_refs", ()):
        requirements, _ = activation_requirements(host, seat, source, ability, targets=(ref,))
        vector = tuple(sorted(requirements.items()))
        if vector not in max_by_requirements:
            max_by_requirements[vector] = _maximum_payable_count(
                host, seat, mana_sources, artifact_ids, requirements, spend_context,
            )
        maximum = max_by_requirements[vector]
        if not maximum or ref not in target_view(maximum).get("legal_refs", ()):
            continue
        lower, upper = 1, maximum
        while lower < upper:
            middle = (lower + upper) // 2
            if ref in target_view(middle).get("legal_refs", ()):
                upper = middle
            else:
                lower = middle + 1
        bounds[ref] = {"minimum": lower, "maximum": maximum}
    if not bounds:
        return None
    group, = public["groups"]
    refs = list(bounds)
    return {**public, "legal_refs": refs, "groups": [{**group, "legal_refs": refs}],
            "x_bound_group": group["id"], "x_bounds_by_ref": bounds}


def validate_announced_artifact_payment(host, seat, source, ability, response, requirements, value):
    choice, = ability.choices
    refs = response.get("cost_cards") or response.get("cost_objects") or ()
    cards = prepare_selected_zone_change_cost(
        host, actor=seat, cost=choice.fixed_zone_change_cost(), refs=refs, required_count=value,
    )
    if not host._cost_is_affordable(
        seat, requirements, exclude_sources={card.object_id for card in cards},
        spend_context=_spend_context(host, source),
    ):
        raise GameRuleError("The selected artifacts cannot also pay mana for the activation")
