"""Bind public APNAP choices for a consumed next-draw permanent return."""

from collections.abc import Mapping

from ..next_draw_replacement_model import PUBLIC_PERMANENT_RETURN_RESULT
from ..object_predicate import ObjectQuerySpec
from .model import DrawError

RETURN_OFFERED = "scheduled_permanent_return_offered"


def scheduled_permanent_return_effect(controller):
    return {
        "op": "choose_cards_apnap",
        "players": "all",
        "actor": controller,
        "zone": "battlefield",
        "predicate": ObjectQuerySpec(zones=("battlefield",)).to_dict(),
        "then": "return_owner_hand",
        "count": 1,
        "hidden": False,
        "prompt": "Choose a permanent you control to return to its owner’s hand.",
    }


def scheduled_permanent_return_candidates(host, effect, player_order):
    return {
        seat: {ref: host._resolve_object(seat, ref, zones={"battlefield"}, controlled_only=True).logical_object_id
               for ref in host._choice_options(seat, effect)}
        for seat in player_order
    }


def require_scheduled_permanent_return(host, effect, resume):
    from .scheduled_draw_coordination import SCHEDULED_DRAW_RESUME, ScheduledDrawContinuation

    if set(resume) != {SCHEDULED_DRAW_RESUME, RETURN_OFFERED}:
        raise DrawError("A scheduled permanent return requires its consumed draw and offered objects")
    step = ScheduledDrawContinuation.from_dict(resume[SCHEDULED_DRAW_RESUME])
    step.validate(host)
    if (step.grant.result.kind != PUBLIC_PERMANENT_RETURN_RESULT
            or effect != scheduled_permanent_return_effect(step.grant.controller)):
        raise DrawError("The public return lost its exact scheduled draw result")
    seats = host.apnap_order()
    current = scheduled_permanent_return_candidates(host, effect, seats)
    offered = resume[RETURN_OFFERED]
    if not isinstance(offered, Mapping) or offered != current or not any(current.values()):
        raise DrawError("A scheduled return's public objects changed before commit")
    return step, seats, current


def selected_scheduled_permanent_returns(host, choice_state):
    step, seats, offerings = require_scheduled_permanent_return(host, choice_state["effect"], choice_state["resume"])
    selected = choice_state["selected"]
    if choice_state["player_order"] != seats or not isinstance(selected, Mapping) or set(selected) != set(seats):
        raise DrawError("A scheduled return requires every active player's choice in APNAP order")
    result = []
    for seat in seats:
        refs = selected[seat]
        available = offerings[seat]
        if (not isinstance(refs, (list, tuple)) or len(refs) != min(1, len(available))
                or any(type(ref) is not str or ref not in available for ref in refs)):
            raise DrawError("A scheduled return requires the player's originally offered permanent")
        result.extend(refs)
    if len(result) != len(set(result)):
        raise DrawError("A scheduled return cannot select the same permanent twice")
    return step, tuple(result)
