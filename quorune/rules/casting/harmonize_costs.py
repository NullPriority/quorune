"""Bind one announced-X graveyard alternative to its optional current tap record."""

from ...compiled_flashback import compiled_harmonize_spec
from ...harmonize import BASE_OPTION_ID, FINGERPRINT, TAP_PAYMENT, HarmonizeTapPayment
from ..tap_additional_cost import FixedTapAdditionalCost
from .additional_tap_costs import _candidates


_TAP = FixedTapAdditionalCost(1, ("creature",))


def _payment(host, card):
    power = host._numeric_stat(card.object_id, "power")
    return HarmonizeTapPayment(card.object_id, card.ref, card.logical_object_id, power)


def harmonize_base_options(host, seat, card, response, *, hint, enabled):
    if not enabled or card.owner != seat or card.zone != "graveyard":
        return []
    spec = compiled_harmonize_spec(host, card)
    if spec is None:
        return []
    x = response.get("x")
    if x is not None and (type(x) is not int or x < 0):
        return []
    base = spec.option(0 if x is None else x)
    selected = response.get("tap_cost_cards")
    if not hint and selected is not None and (
        type(selected) is not list or len(selected) > 1 or any(type(ref) is not str for ref in selected)
    ):
        return []
    options = [base] if hint or not selected else []
    if not hint and not selected:
        return options
    for candidate in _candidates(host, seat, _TAP, card.object_id):
        if not hint and selected != [candidate.ref]:
            continue
        try:
            payment = _payment(host, candidate)
        except ValueError:
            continue
        options.append({
            **base, "id": f"{BASE_OPTION_ID}:{candidate.ref}",
            "label": f"Harmonize by tapping {candidate.ref}",
            TAP_PAYMENT: payment.to_dict(), "_additional_option_costs": [_TAP.to_descriptor()],
        })
    return options


def validate_harmonize_cast(host, proposal, card, option):
    option_id = str(option.get("id") or "")
    marked = FINGERPRINT in option or TAP_PAYMENT in option
    if option_id.split(":", 1)[0] != BASE_OPTION_ID:
        if marked:
            raise ValueError("Harmonize authority belongs only to its alternate cast")
        return
    spec = compiled_harmonize_spec(host, card)
    if (
        proposal.origin != "graveyard" or card.zone != "graveyard" or card.owner != proposal.seat
        or option.get("source_zone") != "graveyard" or spec is None
        or option.get(FINGERPRINT) != spec.fingerprint
    ):
        raise ValueError("The Harmonize source changed before cast commit")
    if TAP_PAYMENT not in option:
        if option_id != BASE_OPTION_ID:
            raise ValueError("The Harmonize tap selection is absent")
        return
    selected = HarmonizeTapPayment.from_dict(option[TAP_PAYMENT])
    if option_id != f"{BASE_OPTION_ID}:{selected.ref}":
        raise ValueError("The Harmonize option and reserved creature disagree")
    for candidate in _candidates(host, proposal.seat, _TAP, card.object_id):
        if selected.matches(candidate) and _payment(host, candidate) == selected:
            return
    raise ValueError("The Harmonize creature's current eligibility or power changed before cast commit")
