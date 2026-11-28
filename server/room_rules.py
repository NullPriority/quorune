from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
from typing import Any, Callable, Iterable, Mapping

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession, PreparedDeckRules, PreparedSessionRules

from .deck_readiness import assess_deck, has_integrity_blocker
from .store import RoomStartSpec, ServerStore, StoreConflict, StoreForbidden, StoreNotFound


BROWSER_REVIEW_PROFILE = "commander_review"
MAX_PREPARED_DECKS = 16
LOG = logging.getLogger(__name__)
Roster = tuple[tuple[str, str, str, int], ...]


class RoomRulesWarmup:
    """Prepare at most one exact ready room off the HTTP event loop."""

    def __init__(self, store: ServerStore, card_db: CardDatabase) -> None:
        self._store = store
        self._card_db = card_db
        self._key: tuple[str, Roster] | None = None
        self._task: asyncio.Task[PreparedSessionRules | None] | None = None
        self._submitted_decks: OrderedDict[str, PreparedDeckRules] = OrderedDict()

    def review_deck(
        self, deck: DeckDefinition, *, retained_snapshots: Callable[[], Iterable[CardDatabase]],
    ) -> tuple[dict[str, Any], PreparedDeckRules | None]:
        registry = SemanticRegistry()
        checked: dict[str, Any] = {}
        report = assess_deck(
            self._card_db, deck, retained_snapshots=retained_snapshots,
            registry=registry, checked_card_programs=checked,
        )
        prepared = None if has_integrity_blocker(report) else PreparedDeckRules.from_preflight(
            self._card_db, deck, registry, report, checked,
        )
        return report, prepared

    def remember_deck(self, deck_id: str, prepared: PreparedDeckRules | None) -> None:
        if prepared is None:
            return
        self._submitted_decks[deck_id] = prepared
        if len(self._submitted_decks) > MAX_PREPARED_DECKS:
            self._submitted_decks.popitem(last=False)

    def _checked_roster(self, roster: Roster) -> tuple[PreparedDeckRules, ...] | None:
        deck_ids = tuple(deck_id for _, _, deck_id, _ in roster)
        if not self._submitted_decks.keys() >= set(deck_ids):
            return None
        return tuple(self._submitted_decks[deck_id] for deck_id in deck_ids)

    def schedule(self, room: Mapping[str, Any]) -> None:
        if room["status"] != "open" or room["spectator"]:
            return
        occupied = [seat for seat in room["seats"] if seat["guest_id"] is not None]
        if len(occupied) < (2 if room["seat_count"] == 2 else 3) or any(
            not seat["ready"] or seat["deck"] is None for seat in occupied
        ):
            return
        roster = tuple(
            (seat["seat"], seat["guest_id"], seat["deck"]["deck_id"], int(seat["ready"]))
            for seat in occupied
        )
        room_id = room["room_id"]
        key = (room_id, roster)
        if key == self._key or (self._task is not None and not self._task.done()):
            return
        self._queue(key, owner=room["owner_guest_id"], submitted=self._checked_roster(roster))

    def _queue(
        self, key: tuple[str, Roster], *, owner: str = "", spec: RoomStartSpec | None = None,
        submitted: tuple[PreparedDeckRules, ...] | None,
    ) -> asyncio.Task[PreparedSessionRules | None]:
        room_id, roster = key
        task = asyncio.create_task(
            asyncio.to_thread(self._prepare, room_id, owner, roster, spec, submitted),
            name=f"room-rules-warmup-{room_id}",
        )
        self._key, self._task = key, task
        return task

    def _prepare(
        self, room_id: str, owner: str, roster: Roster, spec: RoomStartSpec | None,
        submitted: tuple[PreparedDeckRules, ...] | None,
    ) -> PreparedSessionRules | None:
        try:
            if spec is None:
                spec = self._store.start_spec(room_id, owner)
            if spec.roster == roster:
                return CommanderSession.prepare_rules(
                    self._card_db, spec.decks, review_profile=BROWSER_REVIEW_PROFILE,
                    submitted_decks=submitted,
                )
        except (StoreConflict, StoreForbidden, StoreNotFound):
            pass
        except Exception as exc:
            # Start still applies the ordinary validation and reports its error.
            LOG.warning("Room rules warmup failed (%s)", type(exc).__name__)
        return None

    async def for_start(self, room_id: str, spec: RoomStartSpec) -> PreparedSessionRules | None:
        key = (room_id, spec.roster)
        task = self._task
        if task is None or self._key != key:
            submitted = self._checked_roster(spec.roster)
            if submitted is None or (task is not None and not task.done()):
                return None
            task = self._queue(key, spec=spec, submitted=submitted)
        prepared = await asyncio.shield(task)
        if self._task is task:
            self._task = None
        return prepared

    async def close(self) -> None:
        if self._task is not None:
            await self._task
