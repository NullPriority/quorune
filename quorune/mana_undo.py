from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Protocol

from .tap_state import set_permanent_tapped
from .util import normalize_mana_bundle


_STAT_KEY = "reversible_mana_activations"


class ManaUndoError(ValueError):
    pass


class ManaUndoHost(Protocol):
    state: Any

    def _check_priority(self, seat: str) -> None: ...

    def _apply_mana_spend(
        self,
        seat: str,
        spent: Mapping[str, int],
        spend_context: str | None,
    ) -> None: ...

    def _resolve_object(
        self, actor: str, ref: str, *, zones: set[str]
    ) -> Any: ...

    def _log(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class ReversibleManaActivation:
    source_object_id: str
    source_logical_object_id: str
    source_ref: str
    ability_id: str
    bundle: tuple[tuple[str, int], ...]
    turn_sequence: int
    phase: str
    step: str
    priority_epoch: int

    def __post_init__(self) -> None:
        if not all(
            (
                self.source_object_id,
                self.source_logical_object_id,
                self.source_ref,
                self.ability_id,
                self.phase,
                self.step,
            )
        ):
            raise ManaUndoError("Mana rollback identity is incomplete")
        if self.turn_sequence < 0 or self.priority_epoch < 0:
            raise ManaUndoError("Mana rollback window values are invalid")
        normalized = normalize_mana_bundle(dict(self.bundle))
        expected = tuple(
            (color, normalized[color])
            for color in "WUBRGC"
            if normalized[color]
        )
        if expected != self.bundle:
            raise ManaUndoError(
                "Mana rollback bundle must be canonical"
            )

    @classmethod
    def create(
        cls,
        *,
        source_object_id: str,
        source_logical_object_id: str,
        source_ref: str,
        ability_id: str,
        bundle: Mapping[str, int],
        turn_sequence: int,
        phase: str,
        step: str,
        priority_epoch: int,
    ) -> "ReversibleManaActivation":
        normalized = normalize_mana_bundle(bundle)
        return cls(
            source_object_id=source_object_id,
            source_logical_object_id=source_logical_object_id,
            source_ref=source_ref,
            ability_id=ability_id,
            bundle=tuple(
                (color, normalized[color])
                for color in "WUBRGC"
                if normalized[color]
            ),
            turn_sequence=int(turn_sequence),
            phase=str(phase),
            step=str(step),
            priority_epoch=int(priority_epoch),
        )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "ReversibleManaActivation":
        expected = {
            "source_object_id",
            "source_logical_object_id",
            "source_ref",
            "ability_id",
            "bundle",
            "turn_sequence",
            "phase",
            "step",
            "priority_epoch",
        }
        if set(value) != expected:
            raise ManaUndoError("Mana rollback entry fields are invalid")
        bundle = value["bundle"]
        if not isinstance(bundle, Mapping):
            raise ManaUndoError("Mana rollback bundle must be an object")
        return cls.create(
            source_object_id=str(value["source_object_id"]),
            source_logical_object_id=str(
                value["source_logical_object_id"]
            ),
            source_ref=str(value["source_ref"]),
            ability_id=str(value["ability_id"]),
            bundle={str(key): int(amount) for key, amount in bundle.items()},
            turn_sequence=int(value["turn_sequence"]),
            phase=str(value["phase"]),
            step=str(value["step"]),
            priority_epoch=int(value["priority_epoch"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_object_id": self.source_object_id,
            "source_logical_object_id": self.source_logical_object_id,
            "source_ref": self.source_ref,
            "ability_id": self.ability_id,
            "bundle": dict(self.bundle),
            "turn_sequence": self.turn_sequence,
            "phase": self.phase,
            "step": self.step,
            "priority_epoch": self.priority_epoch,
        }


def mana_undo_stack(
    stats: Mapping[str, Any],
) -> tuple[ReversibleManaActivation, ...]:
    raw = stats.get(_STAT_KEY, [])
    if not isinstance(raw, list):
        raise ManaUndoError("Mana rollback journal must be a list")
    return tuple(
        ReversibleManaActivation.from_dict(value)
        if isinstance(value, Mapping)
        else (_raise_entry_shape())
        for value in raw
    )


def _raise_entry_shape() -> ReversibleManaActivation:
    raise ManaUndoError("Mana rollback journal entry must be an object")


def store_mana_undo_stack(
    stats: MutableMapping[str, Any],
    entries: tuple[ReversibleManaActivation, ...],
) -> None:
    if entries:
        stats[_STAT_KEY] = [entry.to_dict() for entry in entries]
    else:
        stats.pop(_STAT_KEY, None)


def clear_mana_undo_stack(stats: MutableMapping[str, Any]) -> None:
    stats.pop(_STAT_KEY, None)


def push_mana_undo(
    stats: MutableMapping[str, Any],
    entry: ReversibleManaActivation,
) -> None:
    store_mana_undo_stack(stats, (*mana_undo_stack(stats), entry))


def pop_mana_undo(
    stats: MutableMapping[str, Any],
) -> ReversibleManaActivation:
    entries = mana_undo_stack(stats)
    if not entries:
        raise ManaUndoError("There is no reversible mana activation")
    store_mana_undo_stack(stats, entries[:-1])
    return entries[-1]


def available_mana_undo(
    state: Any, seat: str
) -> ReversibleManaActivation | None:
    try:
        entries = mana_undo_stack(state.players[seat].stats)
    except (KeyError, ManaUndoError):
        return None
    if not entries:
        return None
    if state.pending_trigger_batches:
        return None
    entry = entries[-1]
    source = state.cards.get(entry.source_object_id)
    if (
        source is None
        or source.ref != entry.source_ref
        or source.logical_object_id != entry.source_logical_object_id
        or source.zone != "battlefield"
        or source.controller != seat
        or not source.tapped
        or entry.turn_sequence != state.turn_sequence
        or entry.phase != state.phase
        or entry.step != state.step
        or entry.priority_epoch != state.priority_epoch
        or state.priority_player != seat
    ):
        return None
    pool = state.players[seat].mana_pool
    if any(pool[color] < amount for color, amount in entry.bundle):
        return None
    return entry


def priority_actions_with_mana_undo(
    state: Any, seat: str
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {"id": "pass", "action": "pass", "label": "Pass priority"}
    ]
    entry = available_mana_undo(state, seat)
    if entry is not None:
        actions.append(
            {
                "id": f"undo-mana:{entry.source_ref}",
                "action": "undo_mana",
                "kind": "undo_mana",
                "label": f"Undo mana from {entry.source_ref}",
                "source": entry.source_ref,
            }
        )
    return actions


def undo_mana_activation(
    host: ManaUndoHost,
    seat: str,
    response: Mapping[str, Any],
) -> None:
    """Revert one pure tap-mana activation in its unchanged priority window."""

    host._check_priority(seat)
    entry = available_mana_undo(host.state, seat)
    requested_source = str(response.get("source") or "")
    if entry is None or (
        requested_source and requested_source != entry.source_ref
    ):
        raise ManaUndoError("That mana activation can no longer be undone")
    source = host.state.cards[entry.source_object_id]
    host._apply_mana_spend(seat, dict(entry.bundle), None)
    set_permanent_tapped(
        host,
        source.ref,
        actor=seat,
        tapped=False,
        reason="mana activation rollback",
        revert=True,
        log=False,
    )
    pop_mana_undo(host.state.players[seat].stats)
    host._log(
        seat,
        "mana.undo",
        f"{seat} undid {source.ref}'s mana activation.",
        {
            "source": source.ref,
            "ability": entry.ability_id,
            "bundle": dict(entry.bundle),
        },
        importance=0,
        changed_objects=[source.object_id],
        changed_players=[seat],
    )
