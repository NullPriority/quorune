from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..carddb_characteristics import library_reveal_printed_identity
from ..errors import GameRuleError
from ..replacement import ReplacementChoiceRequired
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.apnap_library_reveals import (
    LOOK_COUNT, LAND, ROLE_ORDER, is_closed_library_reveal_round_program, library_reveal_round_effects,
)
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionError, LibrarySelectionObjectIdentity
from ..semantic_choices.library_selection_intent_commit import commit_apnap_library_reveals
from ..semantic_choices.model import ObjectChoice
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..zone_transitions import ZoneTransitionOwner
from .model import SelectionContinuation, SelectionContract, SelectionModelError, decode_selection_continuation


OPERATION = "selection.nontarget.apnap_library_reveals.v1"
_FRAME = "apnap_library_reveal_frame"
_COMMIT = "_commit_apnap_library_reveals"
_REPLACEMENTS = "_replacement_selections"


def _looked(value):
    if not isinstance(value, list) or len(value) > LOOK_COUNT:
        raise ValueError("A library reveal round requires up to five pinned top cards")
    return tuple(LibrarySelectionObjectIdentity.from_dict(row) for row in value)


def _arrangement(looked, selected):
    selected = tuple(selected)
    if len(selected) > len(ROLE_ORDER):
        raise ValueError("A library reveal round can select no more than one card per role")
    return LibrarySelectionArrangement(
        looked, selected, tuple(card.ref for card in looked if card.ref not in selected), "library_bottom", "random",
    )


@dataclass(frozen=True, slots=True)
class LibraryRevealRound:
    seats: tuple[str, ...]
    completed: tuple[LibrarySelectionArrangement, ...] = ()
    looked: tuple[LibrarySelectionObjectIdentity, ...] | None = None
    selected: tuple[str, ...] = ()
    role: int = 0

    def __post_init__(self):
        if (any(type(seat) is not str or not seat for seat in self.seats) or len(set(self.seats)) != len(self.seats)
                or len(self.completed) > len(self.seats) or type(self.role) is not int or not 0 <= self.role <= len(ROLE_ORDER)
                or len(self.selected) > self.role or self.looked is None and (self.selected or self.role)
                or len(self.completed) == len(self.seats) and self.looked is not None):
            raise ValueError("A library reveal round lost its captured players or role position")
        if self.looked is not None:
            _arrangement(self.looked, self.selected)

    @property
    def done(self):
        return len(self.completed) == len(self.seats)

    @property
    def actor(self):
        return self.seats[len(self.completed)]

    def to_dict(self):
        return {
            "seats": list(self.seats),
            "completed": [{"looked": [row.to_dict() for row in item.looked], "selected": list(item.selected_refs)}
                          for item in self.completed],
            "looked": [row.to_dict() for row in self.looked] if self.looked is not None else None,
            "selected": list(self.selected), "role": self.role,
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"seats", "completed", "looked", "selected", "role"}
                or any(not isinstance(value[field], list) for field in ("seats", "completed", "selected"))
                or any(not isinstance(row, Mapping) or set(row) != {"looked", "selected"}
                       or not isinstance(row["selected"], list) for row in value["completed"])):
            raise ValueError("A library reveal continuation is malformed")
        completed = tuple(_arrangement(_looked(row["looked"]), row["selected"]) for row in value["completed"])
        looked = _looked(value["looked"]) if value["looked"] is not None else None
        return cls(tuple(value["seats"]), completed, looked, tuple(value["selected"]), value["role"])


def _keep(item, frame):
    item.context[_FRAME] = frame.to_dict()


def _current_frame(item):
    try:
        return LibraryRevealRound.from_dict(item.context.get(_FRAME))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def _require_item(host, effect, resume):
    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or item.ref != resume["stack_ref"] or item.kind not in {"spell", "spell_copy"}
            or item.targets or item.context.get("currently_resolving") is not True):
        raise GameRuleError("The original untargeted library reveal spell is unavailable")
    host._validate_semantic_frame(resume["semantic_frame"], item)
    program = host.semantics.get(item.semantic_key)
    choice, life = library_reveal_round_effects()
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_library_reveal_round_program(program)
            or {key: val for key, val in effect.items() if key not in {_COMMIT, _REPLACEMENTS}} != choice
            or thaw_value(resume["effects"]) != [life]):
        raise GameRuleError("The library reveal round lost its original choice and fixed life instruction")
    return item


def _prefix(host, seat):
    cards = host.state.cards
    library = host.state.players[seat].zones["library"]
    return tuple(LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
                 for card in (cards[object_id] for object_id in reversed(library[-LOOK_COUNT:])))


def _printed_card(host, identity):
    card = host.state.cards[identity.object_id]
    return library_reveal_printed_identity(host.card_db.by_oracle_id(card.oracle_id))


def _legal_refs(host, frame):
    required = {"land"} if ROLE_ORDER[frame.role] == LAND else {"instant", "sorcery"}
    return tuple(identity.ref for identity in frame.looked if identity.ref not in frame.selected
                 and required.intersection(host._type_parts(_printed_card(host, identity)[1])[0]))


def _prior_reveals(host, frame):
    return [
        {"player": seat, "cards": [{"ref": row.ref, "name": _printed_card(host, row)[0]}
                                    for row in sorted(completed.looked, key=lambda row: row.ref)
                                    if row.ref in completed.selected_refs]}
        for seat, completed in zip(frame.seats, frame.completed)
    ]


def _validate_completed_choices(host, frame):
    for seat, arrangement in zip(frame.seats, frame.completed, strict=True):
        if seat not in host.active_seats:
            continue
        if arrangement.looked != _prefix(host, seat):
            raise GameRuleError("A completed private look no longer matches its original library")
        picked = [host._type_parts(_printed_card(host, row)[1])[0] for row in arrangement.looked
                  if row.ref in arrangement.selected_refs]
        if (any(not {"land", "instant", "sorcery"}.intersection(types) for types in picked)
                or len(picked) == 2 and not any("land" in land and {"instant", "sorcery"}.intersection(spell)
                                               for land, spell in (picked, picked[::-1]))):
            raise GameRuleError("A completed library reveal lost its two distinct printed roles")


def _issue(host, item, frame, effect, resume, legal):
    actor = frame.actor
    selection = SelectionContinuation(
        contract=SelectionContract.NONTARGET_CHOICE, operation_id=OPERATION, actor=actor,
        state_revision=host.state.revision, stack_ref=item.ref, source_ref=host._stack_source_ref(item),
        visibility="actor_private", payload=FrozenMap({"effect": effect, "resume": resume, "legal_refs": legal}),
    )
    role = "a land" if ROLE_ORDER[frame.role] == LAND else "an instant or sorcery"
    choice = ObjectChoice("cards", legal, ("library",), minimum=0, maximum=1, optional=True, visibility="actor_private")
    host.permissions.issue(
        kind="choice.apnap", role="pilot", actors=[actor], allowed_actions=["choose"],
        payload_by_actor={actor: {
            "prompt": f"You may reveal {role} from the cards you looked at.",
            "looked": [{"ref": row.ref, "name": _printed_card(host, row)[0]} for row in frame.looked],
            "previous_reveals": _prior_reveals(host, frame),
            "legal_actions": [{"id": "choose", "action": "choose", "choice_schema": choice.choice_schema()}],
        }},
        continuation={"selection": selection.to_dict()},
    )


def _commit(host, item, frame, effect, resume):
    _validate_completed_choices(host, frame)
    try:
        commit_apnap_library_reveals(host, tuple((seat, arrangement) for seat, arrangement in
                                    zip(frame.seats, frame.completed, strict=True) if seat in host.active_seats),
                                    source_stack_ref=item.ref, reason=item.label,
                                    replacement_selections=tuple(effect.get(_REPLACEMENTS, ())))
    except ReplacementChoiceRequired as required:
        from ..replacement_decisions import issue_replacement_order_choice

        issue_replacement_order_choice(
            host, item=item, effect={**effect, _COMMIT: 1}, remaining=resume["effects"], destination=resume["destination"],
            note=resume["note"], instruction_pointer=resume["semantic_frame"]["instruction_pointer"], required=required,
        )
        return
    except LibrarySelectionError as exc:
        raise GameRuleError(str(exc)) from exc
    host._continue_resolution(
        stack_ref=item.ref, effects=resume["effects"], destination=resume["destination"], note=resume["note"],
        instruction_pointer=resume["semantic_frame"]["instruction_pointer"] + 1,
    )


def _advance(host, item, frame, effect, resume):
    while not frame.done:
        actor = frame.actor
        if actor not in host.active_seats:
            frame = LibraryRevealRound(frame.seats, (*frame.completed, _arrangement((), ())))
            continue
        if frame.looked is None:
            looked = _prefix(host, actor)
            if looked:
                host.reveal_library_cards_intent(RevealLibraryCardsIntent(
                    actor, actor, actor, tuple(row.ref for row in looked), item.label, public=False,
                ))
            frame = replace(frame, looked=looked)
        if frame.role < len(ROLE_ORDER):
            legal = _legal_refs(host, frame)
            if legal:
                _keep(item, frame)
                _issue(host, item, frame, effect, resume, legal)
                return
            frame = replace(frame, role=frame.role + 1)
            continue
        arrangement = _arrangement(frame.looked, frame.selected)
        ids = tuple(row.object_id for row in frame.looked if row.ref in frame.selected)
        if ids:
            ZoneTransitionOwner(host).reveal_selected_library_cards(actor, ids)
        frame = LibraryRevealRound(frame.seats, (*frame.completed, arrangement))
    _keep(item, frame)
    _commit(host, item, frame, effect, resume)


def begin_library_reveal_round(host, effect, resume):
    item = _require_item(host, effect, resume)
    if _COMMIT in effect:
        frame = _current_frame(item)
        if type(effect[_COMMIT]) is not int or effect[_COMMIT] != 1 or _REPLACEMENTS not in effect or not frame.done:
            raise GameRuleError("The library reveal round cannot repeat its accepted choices")
        _commit(host, item, frame, effect, resume)
        return
    if _FRAME in item.context or _REPLACEMENTS in effect:
        raise GameRuleError("The library reveal collection has already begun")
    _advance(host, item, LibraryRevealRound(tuple(host.apnap_order())), effect, resume)


def complete_library_reveal_choice(host, decision):
    try:
        selection = decode_selection_continuation(
            decision.continuation, expected_contract=SelectionContract.NONTARGET_CHOICE, expected_operation_id=OPERATION,
        )
    except SelectionModelError as exc:
        raise GameRuleError(str(exc)) from exc
    actor, = decision.actors
    payload = thaw_value(selection.payload)
    effect, resume = payload["effect"], payload["resume"]
    item = _require_item(host, effect, resume)
    frame = _current_frame(item)
    if (frame.done or frame.looked is None or frame.role >= len(ROLE_ORDER) or frame.actor != actor
            or actor != selection.actor or selection.stack_ref != item.ref or selection.source_ref != host._stack_source_ref(item)
            or selection.state_revision != decision.created_revision or selection.visibility != "actor_private"
            or frame.looked != _prefix(host, actor)):
        raise GameRuleError("The current private reveal choice is no longer available")
    legal = _legal_refs(host, frame)
    chosen = decision.responses[actor].get("cards", [])
    if (list(legal) != payload["legal_refs"] or not isinstance(chosen, (tuple, list)) or len(chosen) > 1
            or any(type(ref) is not str or ref not in legal for ref in chosen)):
        raise GameRuleError("Reveal up to one eligible card from the original look for this role")
    _advance(host, item, replace(frame, selected=(*frame.selected, *chosen), role=frame.role + 1), effect, resume)
