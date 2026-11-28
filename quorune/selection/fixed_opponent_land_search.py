from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..life_state import LifeStateError, prepare_life_payment
from ..replacement import ReplacementChoiceRequired
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.fixed_opponent_land_search import FIELD, opponent_land_search_effect, opponent_land_search_program_dependencies
from ..rules.library_search_rules import (
    controlled_own_library_search, library_search_constraint, library_search_object_ids,
)
from ..rules.library_selection import LibrarySelectionObjectIdentity
from .model import SelectionContinuation, SelectionContract, SelectionModelError, decode_selection_continuation


OFFER_OPERATION = "selection.nontarget.opponent_land_search.v1"
SEARCH_ROLE = "_coordinated_land_search_role"
_STATE = "coordinated_land_search"
_PHASE = "_land_search_phase"
_REPLACEMENTS = "_replacement_selections"


@dataclass(frozen=True, slots=True)
class OpponentLandFind:
    player: str
    identity: LibrarySelectionObjectIdentity | None
    entry_pay_life: bool = False
    exile_recipient: str | None = None

    def __post_init__(self):
        if (type(self.player) is not str or not self.player or type(self.entry_pay_life) is not bool
                or self.exile_recipient is not None and (type(self.exile_recipient) is not str or not self.exile_recipient)
                or self.identity is None and (self.entry_pay_life or self.exile_recipient is not None)):
            raise ValueError("A staged land find has invalid player, payment or replacement data")

    def to_dict(self):
        return {"player": self.player, "identity": self.identity.to_dict() if self.identity else None,
                "entry_pay_life": self.entry_pay_life, "exile_recipient": self.exile_recipient}

    @classmethod
    def from_dict(cls, row):
        if not isinstance(row, Mapping) or set(row) != {"player", "identity", "entry_pay_life", "exile_recipient"}:
            raise ValueError("A staged land find has invalid fields")
        return cls(
            row["player"], LibrarySelectionObjectIdentity.from_dict(row["identity"]) if row["identity"] is not None else None,
            row["entry_pay_life"], row["exile_recipient"],
        )


def _require_item(host, *, ref=None):
    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or ref is not None and item.ref != ref or item.kind not in {"spell", "spell_copy"}
            or item.controller not in host.active_seats or item.targets or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Coordinated land searches require the original resolving spell")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not opponent_land_search_program_dependencies(program)):
        raise GameRuleError("Coordinated land searches require their complete admitted program")
    return item


def _expected_opponents(host, item):
    return [seat for seat in host.apnap_order() if seat != item.controller]


def _require_state(host, item, phase):
    state = item.context.get(_STATE)
    if (not isinstance(state, dict) or set(state) != {
            "phase", "opponents", "offers", "finds", "controller_view", "controller_searched", "bonus_issued", "searchers",
        } or state["phase"] != phase or state["opponents"] != _expected_opponents(host, item)
            or not isinstance(state["offers"], list)
            or any(type(choice) is not bool for choice in state["offers"])
            or len(state["offers"]) > len(state["opponents"])):
        raise GameRuleError("Coordinated land search continuation changed")
    return state


def _accepted(state):
    return [player for player, accepted in zip(state["opponents"], state["offers"]) if accepted]


def _continuation_effect(phase):
    return {**opponent_land_search_effect(), _PHASE: phase}


def _continue(host, resume, phase=None):
    effects = list(resume["effects"])
    if phase is not None:
        effects.insert(0, _continuation_effect(phase))
    host._continue_resolution(
        stack_ref=resume["stack_ref"], effects=effects, destination=resume["destination"], note=resume["note"],
        instruction_pointer=resume["semantic_frame"]["instruction_pointer"] + int(phase is None),
    )


def _land_search_effect(player, role):
    return {
        "op": "search", "zone": "library", "selector": {"types": ["land"]},
        "count": {"minimum": 1, "maximum": 1}, "destination": "battlefield",
        "shuffle_after": False, "searching_player": player, SEARCH_ROLE: role,
    }


def _issue_search(host, item, resume, state, player, role):
    host._begin_semantic_search(
        item=item, effect=_land_search_effect(player, role), remaining=[_continuation_effect(state["phase"]), *resume["effects"]],
        destination=resume["destination"], note=resume["note"],
        instruction_pointer=resume["semantic_frame"]["instruction_pointer"],
    )


def _record_controller_view(host, item, state):
    constraint = library_search_constraint(host, item.controller)
    state["controller_searched"] = not constraint.prohibited
    if not constraint.prohibited:
        if constraint.top_count is not None:
            ids = library_search_object_ids(host, item.controller, "library")
            state["controller_view"] = [
                LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref).to_dict()
                for object_id in ids if (card := host.state.cards.get(object_id)) is not None
            ]
        if item.controller not in state["searchers"]:
            state["searchers"].append(item.controller)


def _search_window(host, effect, player):
    role = effect.get(SEARCH_ROLE)
    item = _require_item(host)
    state = item.context.get(_STATE, {})
    phase = state.get("phase")
    if role == "opponent" and phase == "opponent_search":
        accepted = _accepted(state)
        index = len(state["finds"])
        expected = accepted[index] if index < len(accepted) else None
    else:
        expected = item.controller if role == "controller" and phase in {"offers", "bonus"} else None
    descriptor = {key: value for key, value in effect.items() if key not in {"_search_completion", _REPLACEMENTS}}
    if player != expected or thaw_value(descriptor) != _land_search_effect(player, role):
        raise GameRuleError("The private land search changed its original stage or player")
    if role != "controller" or state["controller_view"] is None:
        return None
    identities = tuple(LibrarySelectionObjectIdentity.from_dict(row) for row in state["controller_view"])
    return frozenset(identity.ref for identity in identities
                     if (card := host.state.cards.get(identity.object_id)) is not None
                     and card.zone == "library" and card.owner == player and card.ref == identity.ref
                     and card.logical_object_id == identity.logical_object_id)


def filter_coordinated_search_rows(host, player, effect, options):
    if SEARCH_ROLE not in effect:
        return options
    eligible = _search_window(host, effect, player)
    return options if eligible is None else [option for option in options if option["id"] in eligible]


def _matching_search_controller(host, item, player, controller):
    control = controlled_own_library_search(host, item, player, ("library",))
    return control is not None and control.controller is not None and control.controller.controller == controller


def coordinated_search_private_view(host, item, seat, effect, view):
    if SEARCH_ROLE not in effect:
        return view
    if "search_window" in view:
        view = {**view, "search_window": filter_coordinated_search_rows(host, seat, effect, view["search_window"])}
    if effect[SEARCH_ROLE] != "opponent":
        return view
    state = _require_state(host, item, "opponent_search")
    control = controlled_own_library_search(host, item, seat, ("library",))
    if control is None or control.controller is None:
        return view
    seats = [player for player in _accepted(state) if _matching_search_controller(host, item, player, control.controller.controller)]
    if len(seats) < 2:
        return view
    return {**view, "parallel_search_views": {
        player: [
            {"id": card.ref, "name": card.printed_name}
            for object_id in library_search_object_ids(host, player, "library")
            if (card := host.state.cards.get(object_id)) is not None
        ] for player in seats
    }}


def _offer_next(host, item, state, resume):
    index = len(state["offers"])
    while index < len(state["opponents"]) and library_search_constraint(host, state["opponents"][index]).prohibited:
        state["offers"].append(False)
        index += 1
    if index == len(state["opponents"]):
        state["phase"] = "opponent_search"
        _continue(host, resume, "opponent_search")
        return
    player = state["opponents"][index]
    selection = SelectionContinuation(
        contract=SelectionContract.NONTARGET_CHOICE, operation_id=OFFER_OPERATION, actor=player,
        state_revision=host.state.revision, stack_ref=item.ref, source_ref=host._stack_source_ref(item),
        visibility="public", payload=FrozenMap({"resume": resume, "index": index}),
    )
    host.permissions.issue(
        kind="choice.apnap", role="pilot", actors=[player], allowed_actions=["choose"],
        payload_by_actor={player: {
            "prompt": "Choose whether to search your library for a land card.",
            "prior_search_choices": [
                {"player": seat, "search": accepted}
                for seat, accepted in zip(state["opponents"], state["offers"])
            ],
            "legal_actions": [{
                "id": "choose", "action": "choose",
                "choice_schema": {"field": "choice", "legal_values": ["search", "decline"]},
            }],
        }},
        continuation={"selection": selection.to_dict()},
    )


def complete_opponent_land_search_offer(host, decision):
    try:
        selection = decode_selection_continuation(
            decision.continuation, expected_contract=SelectionContract.NONTARGET_CHOICE, expected_operation_id=OFFER_OPERATION,
        )
    except SelectionModelError as exc:
        raise GameRuleError(str(exc)) from exc
    item = _require_item(host, ref=selection.stack_ref)
    state = _require_state(host, item, "offers")
    player, = decision.actors
    payload = thaw_value(selection.payload)
    index = len(state["offers"])
    if (selection.actor != player or selection.state_revision != decision.created_revision
            or selection.source_ref != host._stack_source_ref(item) or selection.visibility != "public"
            or index >= len(state["opponents"]) or state["opponents"][index] != player or index != payload.get("index")
            or library_search_constraint(host, player).prohibited):
        raise GameRuleError("The original search offer changed")
    resume = payload["resume"]
    host._validate_semantic_frame(resume["semantic_frame"], item)
    choice = decision.responses[player].get("choice")
    if type(choice) is not str or choice not in {"search", "decline"}:
        raise GameRuleError("Choose whether to search or decline")
    state["offers"].append(choice == "search")
    host._log(player, "library.search.offer", f"{player} chose to {choice}.", {"choice": choice, "source": item.ref},
              importance=2, changed_players=[player])
    _offer_next(host, item, state, resume)


def _validate_entry_payment(host, player, card):
    amount = host._land_entry_life_amount(host.card_record(card))
    if amount <= 0:
        raise GameRuleError("This selected land offers no entry payment")
    try:
        prepare_life_payment(host, player, amount)
    except LifeStateError as exc:
        raise GameRuleError(str(exc)) from exc


def _stage_opponent_find(host, context, refs, recipient):
    state = _require_state(host, context.item, "opponent_search")
    player = context.seat
    if not refs:
        selected = OpponentLandFind(player, None)
    else:
        card = host._resolve_object(player, refs[0], zones={"library"}, owned_only=True)
        identity = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
        payment = context.response.get("entry_pay_life", context.response.get("pay_life", False))
        if type(payment) is not bool:
            raise GameRuleError("Land-entry payment must be an explicit boolean")
        pay_entry = payment and recipient is None
        if pay_entry:
            _validate_entry_payment(host, player, card)
        selected = OpponentLandFind(player, identity, pay_entry, recipient)
    state["finds"].append(selected.to_dict())
    state["searchers"].append(player)
    remaining = context.continuation
    host._continue_resolution(
        stack_ref=context.stack_ref, effects=remaining["remaining"], destination=remaining["destination"],
        note=remaining["note"], instruction_pointer=context.frame["instruction_pointer"],
    )


def complete_staged_opponent_find(host, decision, context, refs, zones):
    from .controlled_search import controlled_find_recipients

    _search_window(host, context.effect, context.seat)
    recipients = controlled_find_recipients(
        host, decision, item=context.item, actor=context.seat, zones=zones, refs=refs,
    )
    if recipients is not None:
        _stage_opponent_find(host, context, refs, recipients[0] if recipients else None)


def _prepared_opponent_finds(host, state):
    try:
        finds = tuple(OpponentLandFind.from_dict(row) for row in state["finds"])
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if [find.player for find in finds] != _accepted(state):
        raise GameRuleError("The simultaneous opponent finds lost their searchers")
    for find in finds:
        if find.identity is None:
            continue
        identity = find.identity
        card = host._resolve_object(find.player, identity.ref, zones={"library"}, owned_only=True)
        if (card.object_id != identity.object_id or card.logical_object_id != identity.logical_object_id
                or not host._search_candidate_matches(card, {"types": ["land"]})
                or find.exile_recipient is not None and find.exile_recipient not in host.active_seats):
            raise GameRuleError("A staged library land identity changed")
    return tuple(find for find in finds if find.identity is not None)


def _commit_simultaneous_finds(host, item, state, effect, resume):
    from ..opponent_library_exile import grant_controlled_search_exile_play
    from ..replacement_decisions import issue_replacement_order_choice
    from ..zone_transitions import ZoneTransitionOwner

    finds = _prepared_opponent_finds(host, state)
    changes = [(find.identity.object_id, "exile" if find.exile_recipient is not None else "battlefield") for find in finds]
    battlefield = [find for find in finds if find.exile_recipient is None]
    try:
        moved = ZoneTransitionOwner(host).move_cards_simultaneously(
            changes, reason=f"{item.label} opponent searches", log=False,
            putting_players={find.identity.object_id: find.player for find in battlefield},
            entry_pay_life={find.identity.object_id: True for find in battlefield if find.entry_pay_life},
            replacement_selections=tuple(effect.get(_REPLACEMENTS, ())),
        )
    except ReplacementChoiceRequired as required:
        issue_replacement_order_choice(
            host, item=item, effect=effect, remaining=resume["effects"], destination=resume["destination"],
            note=resume["note"], instruction_pointer=resume["semantic_frame"]["instruction_pointer"], required=required,
        )
        return
    grants = {find.identity.object_id: find.exile_recipient for find in finds if find.exile_recipient is not None}
    for card in moved:
        if card.zone == "exile" and not card.face_down and card.object_id in grants:
            grant_controlled_search_exile_play(card, grants[card.object_id])
    for player in _accepted(state):
        host._log(player, "library.search", f"{player} finished the opponent land search.", {"source": item.ref},
                  importance=2, changed_players=[player])
    state["phase"] = "bonus"
    _continue(host, resume, "bonus")


def _opponent_search(host, item, state, resume):
    accepted = _accepted(state)
    if len(state["finds"]) == len(accepted):
        state["phase"] = "opponent_commit"
        _continue(host, resume, "opponent_commit")
        return
    player = accepted[len(state["finds"])]
    if library_search_constraint(host, player).prohibited:
        raise GameRuleError("An accepted land search became prohibited before it began")
    _issue_search(host, item, resume, state, player, "opponent")


def _bonus_search(host, item, state, resume):
    if state["bonus_issued"] < len(_accepted(state)):
        state["bonus_issued"] += 1
        if not state["controller_searched"] and not library_search_constraint(host, item.controller).prohibited:
            _record_controller_view(host, item, state)
        _issue_search(host, item, resume, state, item.controller, "controller")
        return
    state["phase"] = "finished"
    for player in host.apnap_order():
        if player in state["searchers"]:
            host.shuffle_library(player, reason=f"{item.label} resolved")
    _continue(host, resume)


def begin_opponent_land_search(host, effect, resume):
    item = _require_item(host, ref=resume["stack_ref"])
    host._validate_semantic_frame(resume["semantic_frame"], item)
    phase = effect.get(_PHASE)
    expected = opponent_land_search_effect() if phase is None else _continuation_effect(phase)
    actual = {key: value for key, value in effect.items() if key != _REPLACEMENTS}
    if (thaw_value(actual) != expected or type(actual.get(FIELD)) is not int
            or _REPLACEMENTS in effect and phase != "opponent_commit"):
        raise GameRuleError("Coordinated land search instruction changed")
    if phase is None:
        if _STATE in item.context:
            raise GameRuleError("The initial land search was already offered")
        state = {
            "phase": "offers", "opponents": _expected_opponents(host, item), "offers": [], "finds": [],
            "controller_view": None, "controller_searched": False, "bonus_issued": 0, "searchers": [],
        }
        item.context[_STATE] = state
        _record_controller_view(host, item, state)
        _issue_search(host, item, resume, state, item.controller, "controller")
        return
    state = _require_state(host, item, phase)
    if phase == "offers":
        _offer_next(host, item, state, resume)
    elif phase == "opponent_search":
        _opponent_search(host, item, state, resume)
    elif phase == "opponent_commit":
        _commit_simultaneous_finds(host, item, state, effect, resume)
    elif phase == "bonus":
        _bonus_search(host, item, state, resume)
    else:
        raise GameRuleError("Unknown coordinated land search phase")
