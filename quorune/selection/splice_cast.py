from __future__ import annotations

"""Private ordered additions before one ordinary authoritative cast."""

from ..effect_runtime.hand_inspection import validate_entry_hand_reveal
from ..errors import GameRuleError
from ..mana_payment_continuations import execute_mana_choice_capable_priority_action
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.casting.model import CastProposalRequest
from ..rules.casting.proposal import _resolve_cast_source, _validate_offer_fingerprint, build_cast_offer
from ..rules.casting.splice_costs import splice_source_identity
from ..rules.splice import SPLICE_SELECTION
from .model import SelectionContinuation, SelectionContract, decode_selection_continuation


_OPERATION = "cast.splice.selection.v1"
_FIELDS = {"source_id", "source_logical_id", "zone", "priority_epoch", "method", "face",
    "modes", "x", "cost_option", "selected"}


def _cast_options(host, seat, card, draft, refs):
    declaration = {"modes": draft["modes"]}
    if draft["x"] is not None:
        declaration["x"] = draft["x"]
    if draft["cost_option"] is not None:
        declaration["cost_option"] = draft["cost_option"]
    if refs:
        declaration[SPLICE_SELECTION] = refs
    result = build_cast_offer(host, seat, card, cast_method=draft["method"], face_name=draft["face"],
        splice_declaration=declaration)
    options = result.offer.to_dict()["cost_options"] if result.offer is not None else ()
    return [option for option in options if draft["cost_option"] in {None, option["id"]}]


def _issue_splice_cast(host, seat, card, draft):
    chosen = tuple(row["ref"] for row in draft["selected"])
    ordinary = build_cast_offer(host, seat, card, cast_method=draft["method"], face_name=draft["face"])
    if ordinary.offer is None:
        raise GameRuleError("The Splice spell is no longer available to cast")
    actions = []
    for ref in ordinary.offer.to_dict().get("splice_candidates", ()):
        if ref in chosen or not _cast_options(host, seat, card, draft, (*chosen, ref)):
            continue
        candidate = host._resolve_object(seat, ref, zones={"hand"}, owned_only=True)
        actions.append({"id": f"add:{ref}", "action": "choose", "choice": f"add:{ref}",
            "card": ref, "label": f"Splice {candidate.printed_name}"})
    options = _cast_options(host, seat, card, draft, chosen)
    if options:
        actions.append({"id": "cast", "action": "choose", "choice": "cast", "label": "Cast with these choices",
            "cost_options": options})
    actions.append({"id": "cancel", "action": "choose", "choice": "cancel", "label": "Return to priority"})
    selection = SelectionContinuation(
        contract=SelectionContract.COST_SELECTION, operation_id=_OPERATION, actor=seat,
        state_revision=host.state.revision, source_ref=card.ref, visibility="actor_private",
        payload=FrozenMap(draft),
    )
    host.permissions.issue(
        kind="selection.splice_cast", role="pilot", actors=[seat], allowed_actions=["choose"],
        payload_by_actor={seat: {"card": card.ref, "selected_splice_sources": list(chosen),
            "prompt": "Choose additional spell text in resolution order.", "legal_actions": actions}},
        continuation={"selection": selection.to_dict()},
    )


def begin_splice_cast(host, seat, response):
    host._check_priority(seat)
    request = CastProposalRequest.from_submission(seat, response)
    card = _resolve_cast_source(host, request)
    _validate_offer_fingerprint(host, request, card)
    face = request.required_face or request.face
    result = build_cast_offer(host, seat, card, cast_method=request.cast_method, face_name=face)
    if result.offer is None or not result.offer.to_dict().get("splice_candidates"):
        raise GameRuleError("This cast has no current Splice sources")
    _issue_splice_cast(host, seat, card, {
        "source_id": card.object_id, "source_logical_id": card.logical_object_id, "zone": card.zone,
        "priority_epoch": host.state.priority_epoch, "method": request.cast_method, "face": face,
        "modes": list(request.modes), "x": request.x_value, "cost_option": request.cost_option_id, "selected": [],
    })


def _restore_splice_cast(host, decision):
    selection = decode_selection_continuation(decision.continuation,
        expected_contract=SelectionContract.COST_SELECTION, expected_operation_id=_OPERATION)
    seat, = decision.actors
    draft = thaw_value(selection.payload)
    if (seat != selection.actor or selection.state_revision != decision.created_revision
        or selection.visibility != "actor_private" or set(draft) != _FIELDS
        or draft["priority_epoch"] != host.state.priority_epoch or host.state.priority_player != seat):
        raise GameRuleError("Splice announcement no longer belongs to this priority window")
    card = host.state.cards.get(draft["source_id"])
    if (card is None or card.logical_object_id != draft["source_logical_id"]
        or card.zone != draft["zone"] or card.ref != selection.source_ref):
        raise GameRuleError("Splice spell changed during announcement")
    if not isinstance(draft["selected"], list) or any(
        not isinstance(row, dict) or row.get("player") != seat for row in draft["selected"]
    ):
        raise GameRuleError("Splice announcement lost its selected identities")
    for row in draft["selected"]:
        validate_entry_hand_reveal(host, row)
    refs = tuple(row["ref"] for row in draft["selected"])
    if len(set(refs)) != len(refs) or card.ref in refs:
        raise GameRuleError("Splice requires distinct sources other than the casting card")
    return seat, card, draft, refs


def complete_splice_cast(host, decision):
    seat, card, draft, refs = _restore_splice_cast(host, decision)
    response = dict(decision.responses[seat])
    choice = response.pop("choice", None)
    offered = next((action for action in decision.payload_by_actor[seat]["legal_actions"]
        if action["choice"] == choice), None)
    if offered is None:
        raise GameRuleError("Splice choice was not offered to the caster")
    if choice == "cancel":
        host._issue_priority(seat)
        return
    if choice.startswith("add:"):
        ref = choice.removeprefix("add:")
        if ref in refs or not _cast_options(host, seat, card, draft, (*refs, ref)):
            raise GameRuleError("The selected card cannot currently be spliced")
        selected = host._resolve_object(seat, ref, zones={"hand"}, owned_only=True)
        _issue_splice_cast(host, seat, card,
            {**draft, "selected": [*draft["selected"], splice_source_identity(selected, seat)]})
        return
    options = _cast_options(host, seat, card, draft, refs)
    selected_option = response.get("cost_option") or draft["cost_option"]
    if selected_option is None and len(options) == 1:
        selected_option = options[0]["id"]
    if selected_option not in {option["id"] for option in options}:
        raise GameRuleError("Select a current casting price for the Splice declaration")
    for key in ("action", "cost_options", "proposal_fingerprint", "expiry_revision"):
        response.pop(key, None)
    response.update(card=card.ref, face=draft["face"], cast_method=draft["method"],
        modes=draft["modes"], x=draft["x"], cost_option=selected_option)
    response["from"] = draft["zone"]
    execute_mana_choice_capable_priority_action(host, seat=seat, action="cast", response=response,
        payment_id=decision.decision_id, splice_sources=tuple(draft["selected"]))
