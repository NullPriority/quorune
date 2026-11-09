from __future__ import annotations

"""A shared allocation over the existing Convoke and Improvise resources."""

from collections.abc import Mapping

from ...casting_payment_keywords import PAYMENT_GRANT_SCOPE_FIELD
from ...convoke import CONVOKE_PAYMENT_SYMBOLS, ConvokeCandidate, canonical_mana_requirements, find_convoke_plan
from ...printed_mana_cost_model import cast_snow_payment_kwargs


MIXED_TAP_PAYMENT_FIELD = "mixed_tap_payment"


def supports_mixed_tap_payment(mechanics):
    return len(mechanics) == 2 and {value.get("kind") for value in mechanics} == {"convoke", "improvise"} and any(
        value.get(PAYMENT_GRANT_SCOPE_FIELD) in {"battlefield", "next_spell"} for value in mechanics
    )


def _recommended_mixed_payment(host, seat, option, *, spend_context, reserved_cards):
    from .costs import _convoke_candidates, _convoke_plan_is_affordable, _improvise_candidates

    reserved = {card.object_id for card in reserved_cards}
    creatures = tuple(candidate for candidate in _convoke_candidates(host, seat) if candidate.object_id not in reserved)
    creature_ids = {candidate.object_id for candidate in creatures}
    # An artifact creature can fill either role. For advice, its Convoke
    # candidate already includes generic payment and all of its colors.
    artifacts = tuple(ConvokeCandidate(card.ref, card.object_id, card.logical_object_id)
        for card in _improvise_candidates(host, seat) if card.object_id not in reserved | creature_ids)
    plan = find_convoke_plan(option["requirements"], (*creatures, *artifacts), affordable=lambda candidate:
        _convoke_plan_is_affordable(host, seat, candidate, spend_context=spend_context,
            reserved_cards=reserved_cards, **cast_snow_payment_kwargs(option)))
    if plan is None:
        return None
    return {
        "convoke_cards": [value.candidate.ref for value in plan.contributions if value.candidate.object_id in creature_ids],
        "improvise_cards": [value.candidate.ref for value in plan.contributions if value.candidate.object_id not in creature_ids],
    }


def apply_tap_payments(host, seat, option, mechanics, response, *, hint, spend_context, selected_cards, choice_schema):
    from .costs import _apply_convoke, _apply_improvise

    if not mechanics:
        return True
    context = dict(spend_context=spend_context, selected_cards=selected_cards, choice_schema=choice_schema)
    if len(mechanics) == 1:
        mechanic = mechanics[0]
        if str(mechanic.get("kind") or "").casefold() == "convoke":
            return _apply_convoke(host, seat, option, response, hint=hint, **context)
        return _apply_improvise(host, seat, option, mechanic, response, hint=hint, **context)

    improvise = next(value for value in mechanics if value.get("kind") == "improvise")
    selections = (_recommended_mixed_payment(host, seat, option, spend_context=spend_context, reserved_cards=selected_cards)
                  if hint else response)
    if selections is None:
        return False
    original = canonical_mana_requirements(option["requirements"])
    if (not _apply_improvise(host, seat, option, improvise, selections, hint=False, **context)
            or not _apply_convoke(host, seat, option, selections, hint=False, **context)):
        return False
    if hint:
        schema = choice_schema["convoke_cards"]
        schema["maximum"] = min(len(schema["legal_refs"]), sum(original[symbol] for symbol in CONVOKE_PAYMENT_SYMBOLS))
        option.setdefault("recommended_payment_refs", {}).update(selections)
    else:
        option[MIXED_TAP_PAYMENT_FIELD] = {"schema_version": 1, "improvise_cards": list(selections.get("improvise_cards") or [])}
    return True


def revalidate_mixed_tap_payment(host, proposal, option):
    if MIXED_TAP_PAYMENT_FIELD not in option:
        return
    from .costs import _improvise_candidates, revalidate_convoke_payment

    value = option[MIXED_TAP_PAYMENT_FIELD]
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "improvise_cards"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("Mixed tap payment snapshot is malformed")
    refs = value["improvise_cards"]
    if not isinstance(refs, list) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
        raise ValueError("Mixed Improvise selections are malformed")
    plan = revalidate_convoke_payment(host, proposal.seat, option)
    current_artifacts = {card.ref for card in _improvise_candidates(host, proposal.seat)}
    if (plan is None or set(refs).intersection(plan.selected_refs)
            or not set(refs) <= set(proposal.tap_cost_refs) or not set(refs) <= current_artifacts):
        raise ValueError("A mixed payment resource changed during mana payment")
