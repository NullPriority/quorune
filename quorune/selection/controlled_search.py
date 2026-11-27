"""Retain a private search principal and replace each actual find independently."""

from copy import deepcopy
from types import SimpleNamespace

from ..errors import GameRuleError
from ..library_search_control import CONTROL_FIELD, RESULT_CHOICE
from ..rules.library_search_rules import controlled_own_library_search


_FIND_FIELD = "found_search_election"
_SEARCH_KINDS = frozenset({"semantic.search", "search.fetch", "semantic.choice"})


def search_control_payload(host, item, searcher, zones, *, library_owner=None):
    control = controlled_own_library_search(host, item, searcher, zones, library_owner=library_owner)
    return {CONTROL_FIELD: control.to_dict()} if control is not None else {}


def is_registered_library_search(effect):
    from ..semantic_choices.artifact_search_exchange import scoped_exchange_search

    return (scoped_exchange_search(effect)
            or effect.get("op") == "transmute_artifact" and effect.get("stage") in {"search", "search_v2", "search_v3"})


def _scope(host, decision, item, actor, zones, library_owner):
    fresh = controlled_own_library_search(host, item, actor, zones, library_owner=library_owner)
    expected = fresh.to_dict() if fresh is not None else None
    if decision.continuation.get(CONTROL_FIELD) != expected:
        raise GameRuleError("The controlled search changed its searcher or current sources")
    return fresh


def _offer(host, control, state):
    seat = control.searcher
    refs = state["find_refs"]
    choices = state["choices"]
    if len(choices) >= len(refs):
        resumed = SimpleNamespace(
            kind=state["kind"], decision_id=state["decision_id"], actors=[seat],
            created_revision=state["revision"],
            continuation=deepcopy(state["search"]), responses={seat: deepcopy(state["response"])},
            controlled_find_choices=tuple(choices),
        )
        if resumed.kind == "search.fetch":
            host._complete_fetch_choice(resumed)
        elif resumed.kind == "semantic.search":
            host._complete_semantic_search(resumed)
        else:
            host._complete_registered_semantic_choice(resumed)
        return
    offered_refs = [source.ref for source in control.replacements]
    host.permissions.issue(
        kind=RESULT_CHOICE, role="pilot", actors=[seat], allowed_actions=["choose"],
        payload_by_actor={seat: {
            "prompt": "Choose which effect exiles this card and grants permission to play it.",
            "found_card": refs[len(choices)],
            "options": [{"id": ref, "label": f"Exile with {ref}"} for ref in offered_refs],
        }},
        continuation={CONTROL_FIELD: control.to_dict(), _FIND_FIELD: state},
    )


def complete_controlled_search_election(host, decision):
    from ..library_search_control import pending_library_search_control

    actor, = decision.actors
    control = pending_library_search_control(host.state, decision, actor)
    state = decision.continuation.get(_FIND_FIELD)
    if (control is None or not isinstance(state, dict)
            or set(state) != {"kind", "decision_id", "revision", "search", "response", "find_refs", "choices"}
            or state["kind"] not in _SEARCH_KINDS):
        raise GameRuleError("A found card election requires its retained search occurrence")
    source_ref = decision.responses[actor].get("choice")
    if type(source_ref) is not str or source_ref not in {source.ref for source in control.replacements}:
        raise GameRuleError("Select one of the offered find replacement effects")
    _offer(host, control, {**state, "choices": [*state["choices"], source_ref]})


def resolve_controlled_finds(host, decision, *, item, actor, zones, refs, library_owner=None):
    """Return pending, applied, or unchanged for one original validated search."""
    control = _scope(host, decision, item, actor, zones, library_owner)
    if control is None or not control.replacements or not refs:
        return "unchanged"
    winners = getattr(decision, "controlled_find_choices", None)
    if winners is None:
        candidates = {source.controller for source in control.replacements}
        if len(candidates) > 1:
            _offer(host, control, {
                "kind": decision.kind, "decision_id": decision.decision_id, "revision": decision.created_revision,
                "search": deepcopy(decision.continuation), "response": deepcopy(decision.responses[actor]),
                "find_refs": list(refs), "choices": [],
            })
            return "pending"
        winners = (control.replacements[0].ref,) * len(refs)
    by_ref = {source.ref: source.controller for source in control.replacements}
    if len(winners) != len(refs) or any(winner not in by_ref for winner in winners):
        raise GameRuleError("The found-card replacement changed its selected sources")
    cards = [host._resolve_object(actor, ref, zones=set(zones), owned_only=True) for ref in refs]
    from ..opponent_library_exile import grant_controlled_search_exile_play
    from ..zone_transitions import ZoneTransitionOwner

    grants = {card.object_id: by_ref[winner] for card, winner in zip(cards, winners, strict=True)}
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "exile") for card in cards),
        reason="Exile cards found in the controlled search",
    )
    result = tuple(card for card in moved if card.zone == "exile" and not card.face_down)
    for card in result:
        grant_controlled_search_exile_play(card, grants[card.object_id])
    host._log(actor, "library.search.found_exiled", "The found cards were exiled during the search.",
              {"objects": [card.ref for card in result]}, importance=2,
              changed_objects=[card.object_id for card in result])
    return "applied"
