"""Public nonland exile scanning and opponent election using fixed-top choices."""
from ..discover_model import discover_zone_counter
from ..fixed_top_exile import FIXED_TOP_EXILE_OPERATION
from ..replacement.immutable import FrozenMap
from ..rules.elected_nonland_exile import (
    ELECTED_NONLAND_EXILE_SCAN_VERSION, ELECTED_NONLAND_EXILE_SCOPE, SCAN_FIELD,
    nonland_election_offer, validate_elected_nonland_exile_instruction,
    validate_nonland_election_offer, validate_nonland_scan,
)
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .model import (
    AutoContinue, ObjectChoice, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _identity(row):
    return LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref)


def _wire_rows(rows):
    return [{"owner": owner, "identity": identity.to_dict()} for owner, identity in rows]


def _continue(effect, follow=None, *, intents=(), reason):
    return SemanticChoicePreparation(None, FrozenMap(effect), preparation_intents=intents,
        auto_continue=AutoContinue(reason=reason, prepend_effects=() if follow is None else (FrozenMap(follow),)))


def _committed_tops(query, pending):
    for owner, identity in pending:
        row = query.object(identity.ref, zones=("exile",))
        if row is None:
            before = query.object(identity.ref, zones=("library",))
            if before is not None and before.logical_object_id == identity.logical_object_id:
                raise SemanticChoiceError("Nonland exile made no progress for a selected library top")
            continue
        expected = f"{identity.object_id}@{discover_zone_counter(identity) + 1}"
        if (row.owner != owner or row.object_id != identity.object_id
                or row.logical_object_id != expected or row.token or not row.known_to_actor):
            raise SemanticChoiceError("Nonland exile lost the original public move result")
        yield owner, _identity(row), "land" not in row.types


def prepare_nonland_exile_scan(effect, context):
    query = context.query
    if effect["player"] != context.actor or context.actor != context.stack_controller:
        raise SemanticChoiceError("The public nonland scan belongs to the resolving controller")
    if SCAN_FIELD in effect:
        instruction, pending, captured, completed = validate_nonland_scan(effect)
        committed = tuple(_committed_tops(query, pending))
        captured = (*captured, *((owner, identity) for owner, identity, _ in committed))
        completed = (*completed, *(owner for owner, _, nonland in committed if nonland))
    else:
        validate_elected_nonland_exile_instruction(effect, resolved=True)
        instruction, captured, completed = effect, (), ()
    if context.actor not in query.active_seats:
        return _continue(effect, reason="The resolving controller is no longer in the game.")
    pending = []
    for owner in query.apnap_order:
        if owner in completed:
            continue
        refs = query.library_refs(owner, top_first=True)
        if not refs:
            continue
        row = query.object(refs[0], zones=("library",))
        if row is None or row.owner != owner or row.token:
            raise SemanticChoiceError("Nonland exile requires each current library top")
        pending.append((owner, _identity(row)))
    if not pending:
        return _continue(effect, nonland_election_offer(instruction, captured),
                         reason="The active libraries have finished exiling.")
    follow = {"op": FIXED_TOP_EXILE_OPERATION, "schema_version": ELECTED_NONLAND_EXILE_SCAN_VERSION,
              "scope": ELECTED_NONLAND_EXILE_SCOPE, "player": context.actor, "instruction": instruction,
              SCAN_FIELD: {"pending": _wire_rows(pending), "results": _wire_rows(captured),
                           "completed": list(completed)}}
    move = MoveObjectsSimultaneouslyIntent(context.actor, tuple(identity.ref for _, identity in pending),
        ("library",), "exile", "Exile one current top from each unfinished library")
    return _continue(effect, follow, intents=(move,), reason="Exile the next round of library tops together.")


def _live_nonlands(query, rows):
    candidates = []
    for owner, identity in rows:
        row = query.object(identity.ref, zones=("exile",))
        if (row is not None and row.owner == owner and row.object_id == identity.object_id
                and row.logical_object_id == identity.logical_object_id and row.known_to_actor
                and not row.token and "land" not in row.types):
            candidates.append(row)
    return tuple(candidates)


def prepare_nonland_election(effect, context):
    stage, opponent, _, rows = validate_nonland_election_offer(effect)
    instruction = effect["instruction"]
    controller = instruction["player"]
    if stage == "cast" or controller != context.stack_controller:
        raise SemanticChoiceError("The opponent election must belong to the resolving trigger")
    if context.actor != (opponent if stage == "exclude" else controller):
        raise SemanticChoiceError("The nonland election has a different decision maker")
    if controller not in context.query.active_seats:
        return _continue(effect, reason="The resolving controller is no longer in the game.")
    candidates = _live_nonlands(context.query, rows)
    opponents = tuple(seat for seat in context.query.active_seats if seat != controller)
    if not candidates or not opponents:
        offer = nonland_election_offer(instruction, rows, stage="cast", remaining=2)
        return _continue(effect, offer, reason="There is no nonland card or opponent available to choose.")
    if stage == "elect" and len(opponents) == 1:
        offer = nonland_election_offer(instruction, rows, stage="exclude", opponent=opponents[0])
        return _continue(effect, offer, reason="The only opponent chooses the excluded card.")
    if stage == "exclude" and opponent not in opponents:
        return _continue(effect, nonland_election_offer(instruction, rows),
                         reason="The elected opponent is no longer in the game.")
    if stage == "elect":
        prompt = "Choose an opponent to choose one of the exiled nonland cards."
        choice = ScalarChoice("choice", legal_values=opponents)
        facts = {"opponents": opponents}
    else:
        prompt = "Choose one exiled nonland card to exclude from casting."
        choice = ObjectChoice(field_name="objects", legal_refs=tuple(card.ref for card in candidates),
                              zones=("exile",), owner_relation="any")
        facts = {"objects": [{"id": card.ref, "name": card.printed_name} for card in candidates]}
    return SemanticChoicePreparation(SemanticChoiceRequest(prompt=prompt, choice=choice,
        public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"], **facts})),
        FrozenMap(effect))


def complete_nonland_election(continuation, response, query):
    effect = continuation.effect
    stage, opponent, _, rows = validate_nonland_election_offer(effect)
    instruction = effect["instruction"]
    controller = instruction["player"]
    if controller != continuation.semantic_frame.controller or controller not in query.active_seats:
        raise SemanticChoiceError("The resolving nonland election controller changed")
    fields = set(response) - {"action", "action_id", "choice_schema"}
    if stage == "elect":
        selected = response.get("choice")
        if fields != {"choice"} or selected not in query.active_seats or selected == controller:
            raise SemanticChoiceError("Select a current opponent for the nonland election")
        next_effect = nonland_election_offer(instruction, rows, stage="exclude", opponent=selected)
    elif stage == "exclude":
        selection = response.get("objects")
        candidates = _live_nonlands(query, rows)
        if (opponent not in query.active_seats or fields != {"objects"}
                or not isinstance(selection, (list, tuple)) or len(selection) != 1
                or selection[0] not in {card.ref for card in candidates}):
            raise SemanticChoiceError("Exclude one current nonland result from this resolution")
        allowed = tuple(row for row in rows if row[1].ref != selection[0])
        next_effect = nonland_election_offer(instruction, allowed, stage="cast", opponent=opponent, remaining=2)
    else:
        raise SemanticChoiceError("Casting is completed by the fixed exile cast owner")
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(next_effect),))
