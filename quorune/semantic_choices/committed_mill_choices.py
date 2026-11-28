"""Expose sealed public candidates to the ordinary choice query."""

from ..rules.milled_permanent_battlefield import MILL_PERMANENT_CHOICE, mill_battlefield_choice_candidates
from ..rules.mill_paid_hand_return import MILL_HAND_CHOICE_FIELD, mill_hand_choice
from .model import SemanticChoiceError


def committed_mill_choice_refs(effect, *, actor):
    if effect.get("op") != "choose_option":
        return None
    try:
        if MILL_HAND_CHOICE_FIELD in effect:
            return tuple(row.ref for row in mill_hand_choice(effect, actor=actor))
        if MILL_PERMANENT_CHOICE in effect:
            return tuple(row.ref for row in mill_battlefield_choice_candidates(effect, actor=actor))
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return None
