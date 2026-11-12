from __future__ import annotations

"""Revalidate the declared nonmana resources after mana abilities resolve."""

from ...morph import FACE_DOWN_CAST_METHODS
from ...semantic_runtime.spell_payment_grants import SPELL_PAYMENT_GRANT_FIELD, revalidate_spell_payment_grant
from .model import CastProposalError


def _revalidate_granted_payment(host, proposal, card, option, details):
    uses = option.get(SPELL_PAYMENT_GRANT_FIELD)
    if uses is None:
        return
    from .costs import _improvise_candidates, revalidate_convoke_payment, revalidate_delve_payment

    try:
        revalidate_spell_payment_grant(host, proposal.seat, card, uses, origin=proposal.origin,
            cast_type_line=proposal.type_line, face_down=details.get("cast_method") in FACE_DOWN_CAST_METHODS)
        if "convoke" in uses:
            plan = revalidate_convoke_payment(host, proposal.seat, option)
            if plan is None or set(uses["convoke"]) != set(plan.selected_refs):
                raise ValueError("The granted Convoke selection changed")
        if "delve" in uses:
            revalidate_delve_payment(host, proposal.seat, option)
            paid = option["delve_payment"]["selected"]
            if set(uses["delve"]) != {row["ref"] for row in paid}:
                raise ValueError("The granted Delve selection changed")
        if "improvise" in uses:
            selected = set(uses["improvise"])
            current = {row.ref for row in _improvise_candidates(host, proposal.seat)}
            if not selected <= set(proposal.tap_cost_refs) or not selected <= current:
                raise ValueError("A selected Improvise artifact changed during mana payment")
    except (KeyError, TypeError, ValueError) as exc:
        raise CastProposalError(str(exc), status="unpayable", reason="stale_spell_payment_grant") from exc


def revalidate_resources_after_mana(host, proposal, card, option, details):
    from .additional_tap_costs import validate_fixed_tap_payment
    from .conspire_costs import conspire_tap_cards
    from .emerge_costs import validate_emerge_after_mana_payment
    from .mixed_tap_payment import revalidate_mixed_tap_payment
    from .waterbend_costs import validate_cast_waterbend

    _revalidate_granted_payment(host, proposal, card, option, details)
    try:
        revalidate_mixed_tap_payment(host, proposal, option)
    except ValueError as exc:
        raise CastProposalError(str(exc), status="unpayable", reason="stale_mixed_tap_payment") from exc
    for validate, reason in (
        (validate_emerge_after_mana_payment, "stale_emerge_sacrifice"),
        (conspire_tap_cards, "stale_conspire_after_mana"),
        (validate_fixed_tap_payment, "stale_fixed_tap_after_mana"),
        (validate_cast_waterbend, "stale_waterbend_after_mana"),
    ):
        try:
            validate(host, proposal.seat, card, option)
        except ValueError as exc:
            raise CastProposalError(str(exc), reason=reason) from exc
