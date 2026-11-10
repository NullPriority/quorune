from __future__ import annotations

"""Commit Venture through the existing object and triggered-ability owners."""

from dataclasses import dataclass, replace
from itertools import chain

from .dungeon_catalog import DUNGEON_CATALOG, VentureMode
from .dungeon_progress import (
    DUNGEON_ROOM_EVENT, DUNGEON_VENTURE_FIELD, DungeonJourney, DungeonRoomRef, DungeonVisit, VentureProgressIntent,
    accept_venture, complete_finished_dungeon, prepare_venture, venture_instruction,
)
from .errors import GameRuleError, StateInvariantError
from .game_object_store import GameObjectStore
from .initiative_model import (
    INHERENT_INITIATIVE_TAKE, INHERENT_INITIATIVE_VENTURE, TAKE_INITIATIVE_FIELD,
    initiative_effect_player, initiative_instruction,
)
from .model import CardInstance, StackItem
from .target_source import BOUND_TARGETING_SOURCE_VERSION, TARGETING_SOURCE_CONTEXT, capture_targeting_source
from .trigger_discovery import trigger_multiplier_copies
from .trigger_processing import enqueue_trigger_batch
from .zone_transitions import ZoneTransitionOwner


DUNGEON_COMPLETED_EVENT = "dungeon.completed"
_ORIGINAL_ROOM_TRIGGER = "original_dungeon_room_trigger"


def _source_id(host, owner: str, visit: DungeonVisit) -> str:
    return host._stable_runtime_id("dungeon-source", f"{owner}:{visit.incarnation}")


def _room_program(host, room: DungeonRoomRef):
    program = host.semantics.get(room.program_key)
    definition = DUNGEON_CATALOG[room.dungeon]
    if (program is None or program.event != DUNGEON_ROOM_EVENT or program.active_zone != "command"
            or program.provenance.get("dungeon_room") != room.to_dict()
            or program.provenance.get("source_oracle_hash") != definition.source_oracle_hash
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("The pinned dungeon room program is unavailable")
    return program


def validate_venture_room_offers(host, offer) -> None:
    """Check all legal destinations before offering a venture choice."""
    for room in offer.destinations:
        _room_program(host, room)


def check_dungeon_choice_instruction(host, item, effect) -> None:
    if TAKE_INITIATIVE_FIELD in effect:
        initiative_instruction(effect, actor=initiative_effect_player(item, INHERENT_INITIATIVE_TAKE))
    if DUNGEON_VENTURE_FIELD in effect:
        player = initiative_effect_player(item, INHERENT_INITIATIVE_VENTURE)
        mode = venture_instruction(effect, actor=player)
        if item.semantic_key == INHERENT_INITIATIVE_VENTURE and mode is not VentureMode.UNDERCITY:
            raise GameRuleError("The inherent initiative ability must venture into Undercity")
        if player in host.active_seats:
            current = host.state.players[player].dungeon_journey or DungeonJourney()
            offer, _ = prepare_venture(player, current, mode)
            validate_venture_room_offers(host, offer)


def _current_source(host, owner: str, visit: DungeonVisit) -> CardInstance:
    source = host.state.cards.get(_source_id(host, owner, visit))
    if (source is None or source.object_kind != "dungeon" or source.zone != "command"
            or source.owner != owner or source.controller != owner
            or source.object_id not in host.state.players[owner].zones["command"]):
        raise StateInvariantError("The active dungeon lost its command-zone incarnation")
    return source


def validate_dungeon_membership(host) -> None:
    for owner, player in host.state.players.items():
        active = player.dungeon_journey.active if player.dungeon_journey is not None else None
        sources = tuple(oid for oid in player.zones["command"]
                        if host.state.cards.get(oid) is not None and host.state.cards[oid].object_kind == "dungeon")
        if active is None:
            if sources:
                raise StateInvariantError("An unvisited dungeon appears in the command zone")
        elif not player.in_game or sources != (_current_source(host, owner, active).object_id,):
            raise StateInvariantError("A player must own exactly the active dungeon source")


def _new_source(host, owner: str, visit: DungeonVisit, program) -> CardInstance:
    definition = DUNGEON_CATALOG[visit.room.dungeon]
    source = CardInstance(
        object_id=_source_id(host, owner, visit), ref=host._next_ref("D"), oracle_id=program.oracle_id,
        printed_name=definition.source_name.split(" // ", 1)[0], owner=owner, controller=owner,
        zone="command", object_kind="dungeon", zone_timestamp=host._next_zone_timestamp(),
        known_to=list(host.seats), revealed_to=list(host.seats),
    )
    GameObjectStore(host).commit(upserts=(source,))
    host.state.players[owner].zones["command"].append(source.object_id)
    return source


def _remove_source(host, owner: str, visit: DungeonVisit) -> CardInstance:
    source = _current_source(host, owner, visit)
    ZoneTransitionOwner(host).remove_from_zone(source)
    GameObjectStore(host).commit(removals=(source.object_id,))
    return source


@dataclass(frozen=True, slots=True)
class DungeonCompletion:
    owner: str
    before: DungeonJourney
    after: DungeonJourney
    visit: DungeonVisit


def _commit_completion(host, completion: DungeonCompletion) -> CardInstance:
    player = host.state.players[completion.owner]
    if player.dungeon_journey != completion.before:
        raise StateInvariantError("Dungeon completion changed its original visit")
    source = _remove_source(host, completion.owner, completion.visit)
    player.dungeon_journey = completion.after
    return source


def _publish_completion(host, completion: DungeonCompletion, source: CardInstance) -> None:
    details = {"player": completion.owner, "dungeon": completion.visit.room.dungeon.value,
               "incarnation": completion.visit.incarnation, "object": source.ref}
    host._log(completion.owner, DUNGEON_COMPLETED_EVENT, f"{completion.owner} completed {source.printed_name}.",
              details, importance=2, changed_players=[completion.owner])
    host._dispatch_semantic_event(DUNGEON_COMPLETED_EVENT, details)


def _trigger_room(host, owner: str, source: CardInstance, program) -> None:
    if program.oracle_id != source.oracle_id:
        raise StateInvariantError("The entered room belongs to a different dungeon source")
    ref = host._next_ref("S")
    binding = capture_targeting_source(host, source, schema_version=BOUND_TARGETING_SOURCE_VERSION)
    item = StackItem(
        stack_id=host._stable_runtime_id("stack", ref), ref=ref, kind="triggered_ability",
        controller=owner, label=program.label, source_object_id=source.object_id, semantic_key=program.key,
        visibility=list(host.seats),
        context={"source_logical_object_id": source.logical_object_id, TARGETING_SOURCE_CONTEXT: binding.to_dict(),
                 "source_zone": "command", "event": DUNGEON_ROOM_EVENT, _ORIGINAL_ROOM_TRIGGER: ref,
                 **({"trigger_target_selection_pending": True} if program.target_schema else {})},
    )
    additional = trigger_multiplier_copies(
        host, item=item, source=source, event=DUNGEON_ROOM_EVENT, context={}, active_zone="command",
    )
    for trigger in additional:
        trigger.context[_ORIGINAL_ROOM_TRIGGER] = trigger.ref
    enqueue_trigger_batch(host, (item, *additional))


def commit_venture_progress(host, intent: VentureProgressIntent) -> str:
    owner = intent.offer.owner
    stack = next((item for item in host.state.stack if item.ref == intent.stack_ref), None)
    if (owner not in host.active_seats or stack is None
            or initiative_effect_player(stack, INHERENT_INITIATIVE_VENTURE) != owner
            or not stack.context.get("currently_resolving")):
        raise GameRuleError("Venture lost the active resolving controller")
    player = host.state.players[owner]
    journey = player.dungeon_journey or DungeonJourney()
    if journey != intent.before:
        raise GameRuleError("The current dungeon position changed before venture")
    offer, retired = prepare_venture(owner, journey, intent.offer.mode)
    if offer != intent.offer:
        raise GameRuleError("The current venture offer changed")
    validate_venture_room_offers(host, offer)
    if retired is not None:
        completion = DungeonCompletion(owner, journey, offer.journey, retired)
        _publish_completion(host, completion, _commit_completion(host, completion))
    if intent.selected is None:
        return owner
    entered = accept_venture(owner, offer.journey, offer, intent.selected)
    visit = entered.active
    program = _room_program(host, visit.room)
    source = (_current_source(host, owner, offer.journey.active)
              if offer.journey.active is not None else _new_source(host, owner, visit, program))
    player.dungeon_journey = entered
    host._log(owner, "dungeon.venture", f"{owner} entered {program.label}.",
              {"player": owner, "object": source.ref, "visit": visit.to_dict(), "mode": offer.mode.value},
              importance=2, changed_objects=[source.object_id], changed_players=[owner])
    _trigger_room(host, owner, source, program)
    return owner


def prepare_dungeon_completions(host) -> tuple[DungeonCompletion, ...]:
    terminal = {owner: journey for owner, player in getattr(host.state, "players", {}).items()
                if (journey := getattr(player, "dungeon_journey", None)) is not None
                and journey.active is not None and not journey.active.room.exits}
    if not terminal:
        return ()
    pending = (item for batch in host.state.pending_trigger_batches for item in batch.items)
    outstanding = set()
    for item in chain(host.state.stack, pending):
        facts = item.context if isinstance(item, StackItem) else item.event_facts
        if facts.get(_ORIGINAL_ROOM_TRIGGER) == item.ref:
            outstanding.add(item.source_object_id)
    ready = []
    for owner in host.apnap_order():
        journey = terminal.get(owner)
        if journey is not None and _source_id(host, owner, journey.active) not in outstanding:
            after, visit = complete_finished_dungeon(journey, ())
            ready.append(DungeonCompletion(owner, journey, after, visit))
    return tuple(ready)


def commit_dungeon_completions(host, completions):
    return tuple((completion, _commit_completion(host, completion)) for completion in completions)


def publish_dungeon_completions(host, completed) -> None:
    for completion, source in completed:
        _publish_completion(host, completion, source)


def remove_departing_dungeon(host, owner: str) -> None:
    player = host.state.players[owner]
    journey = player.dungeon_journey
    if journey is not None and journey.active is not None:
        _remove_source(host, owner, journey.active)
        player.dungeon_journey = replace(journey, active=None)
