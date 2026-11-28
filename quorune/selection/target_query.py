from __future__ import annotations

"""Read-only target identity snapshots shared by proposal and revalidation."""

from typing import Any, Iterable, Iterator, Mapping, Protocol, TYPE_CHECKING

from ..errors import GameRuleError
if TYPE_CHECKING:
    from ..model import CardInstance, StackItem


class TargetIdentityQuery(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...


class ObjectIdentityQuery(Protocol):
    state: Any

    def _require_seat(self, seat: str) -> None: ...

    def display_name(self, object_id: str) -> str: ...


def card_ref_index(cards: Iterable[CardInstance]) -> dict[str, list[CardInstance]]:
    refs: dict[str, list[CardInstance]] = {}
    for card in cards:
        refs.setdefault(card.ref.casefold(), []).append(card)
    return refs


def resolve_object(
    host: ObjectIdentityQuery,
    seat: str,
    value: str,
    *,
    zones: Iterable[str] | None = None,
    controlled_only: bool = False,
    owned_only: bool = False,
    indexed_refs: Mapping[str, list[CardInstance]] | None = None,
) -> Any:
    """Resolve explicit identities before evaluating the fallback name query."""
    host._require_seat(seat)
    zone_filter = set(zones) if zones is not None else None
    if value in host.state.cards:
        candidates = [host.state.cards[value]]
    else:
        normalized = value.casefold().strip()
        candidates = indexed_refs.get(normalized, []) if indexed_refs is not None else [
            card for card in host.state.cards.values()
            if card.ref.casefold() == normalized
        ]
        if not candidates:
            from ..rules.private_opponent_top_exile import has_private_top_exile_designation, private_top_exile_viewer

            candidates = [
                card for card in host.state.cards.values()
                if not (card.face_down and has_private_top_exile_designation(card)
                        and seat != private_top_exile_viewer(host.state, card))
                and (card.printed_name.casefold() == normalized
                     or host.display_name(card.object_id).casefold() == normalized)
            ]
    filtered = [
        card for card in candidates
        if card.zone != "outside"
        and (zone_filter is None or card.zone in zone_filter)
        and (not controlled_only or card.controller == seat)
        and (not owned_only or card.owner == seat)
    ]
    if not filtered:
        raise GameRuleError(f"Could not find {value!r} for {seat} in requested zones")
    if len(filtered) > 1:
        options = ", ".join(f"{card.ref}:{card.zone}" for card in filtered)
        raise GameRuleError(f"Ambiguous object {value!r}; use a ref: {options}")
    return filtered[0]


def target_snapshot(host: TargetIdentityQuery, ref: str) -> dict[str, Any]:
    if ref in host.state.players:
        return {
            "ref": ref,
            "category": "player",
            "controller": ref,
            "owner": ref,
            "colors": [],
            "mana_value": 0,
            "type_line": "Player",
        }
    item = next(
        (candidate for candidate in host.state.stack if candidate.ref == ref),
        None,
    )
    if item is not None:
        card = host.state.cards.get(item.card_object_id or "")
        data = host._effective_card_data(card) if card else {}
        return {
            "ref": ref,
            "stack_id": item.stack_id,
            "category": (
                "spell" if item.kind in {"spell", "spell_copy"} else "ability"
            ),
            "controller": item.controller,
            "owner": card.owner if card else item.controller,
            "colors": list(data.get("colors", [])),
            "mana_value": float(
                data.get("mana_value", data.get("cmc", 0)) or 0
            ),
            "type_line": str(data.get("type_line") or ""),
        }
    card = next(
        (
            candidate
            for candidate in host.state.cards.values()
            if candidate.ref == ref
        ),
        None,
    )
    if card is None:
        return {"ref": ref}
    data = host._effective_card_data(card)
    return {
        "ref": ref,
        "object_id": card.object_id,
        "zone_change_counter": card.zone_change_counter,
        "zone": card.zone,
        "category": "permanent" if card.zone == "battlefield" else "card",
        "controller": card.controller,
        "owner": card.owner,
        "colors": list(data.get("colors", [])),
        "mana_value": float(data.get("mana_value", data.get("cmc", 0)) or 0),
        "type_line": str(data.get("type_line") or ""),
    }


def target_identity_matches_snapshot(
    host: TargetIdentityQuery,
    ref: str,
    snapshot: Mapping[str, Any],
) -> bool:
    """Return whether ``ref`` is still the originally selected object."""

    stack_id = snapshot.get("stack_id")
    if stack_id is not None:
        return any(
            item.ref == ref and item.stack_id == stack_id
            for item in host.state.stack
        )
    object_id = snapshot.get("object_id")
    incarnation = snapshot.get("zone_change_counter")
    if object_id is None or incarnation is None:
        # Historical Game Record v3 decisions predate explicit incarnations.
        return True
    card = host.state.cards.get(str(object_id))
    return bool(
        card is not None
        and card.ref == ref
        and card.zone_change_counter == int(incarnation)
    )



def current_spell_target_subjects(
    host: TargetIdentityQuery, item: StackItem,
) -> Iterator[str | CardInstance]:
    """Read current selected subjects for CR 115.9b without rechecking inner legality."""

    snapshots = item.context.get("target_snapshots")
    if not isinstance(snapshots, Mapping):
        return
    announced_groups = item.context.get("target_groups")
    refs = item.targets
    if (item.context.get("targets_revalidated") is True
            and isinstance(announced_groups, Mapping) and announced_groups):
        refs = [
            ref for group in announced_groups.values()
            if isinstance(group, (list, tuple)) for ref in group
        ]
    for ref in refs:
        if type(ref) is not str:
            continue
        snapshot = snapshots.get(ref)
        if not isinstance(snapshot, Mapping) or snapshot.get("ref") != ref:
            continue
        if snapshot.get("category") == "player":
            if ref in host.state.active_seats():
                yield ref
            continue
        if (snapshot.get("zone") != "battlefield" or snapshot.get("category") != "permanent"
                or type(snapshot.get("object_id")) is not str
                or type(snapshot.get("zone_change_counter")) is not int):
            continue
        card = host.state.cards.get(snapshot["object_id"])
        if (card is not None and card.zone == "battlefield" and not card.phased_out
                and target_identity_matches_snapshot(host, ref, snapshot)):
            yield card


__all__ = ["current_spell_target_subjects", "resolve_object", "target_identity_matches_snapshot", "target_snapshot"]
