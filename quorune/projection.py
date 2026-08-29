from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .carddb import CardDatabase
from .characteristic_evaluation import evaluate_card_characteristics
from .choice_forms import build_action_form
from .attachments import attachment_target_ref
from .commander import commander_damage_source
from .continuous_effect_state import active_resolution_effects
from .counter_state import player_counter_snapshot
from .model import (
    CardInstance,
    Event,
    GameState,
    PlayerState,
    PLAYER_COUNTERS_FIELD,
)
from .protocol import PROTOCOL_VERSION, json_patch, view_hash
from .util import stable_json, truncate


def _commander_damage_rows(
    state: GameState,
    received: Mapping[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for damage_key, amount in sorted(received.items()):
        if amount <= 0:
            continue
        source = commander_damage_source(state, damage_key)
        rows.append(
            {
                "cid": (
                    damage_key[:8]
                    if source is None or source.legacy_oracle_identity
                    else damage_key
                ),
                "n": source.printed_name if source is not None else damage_key[:8],
                **(
                    {"owner": source.owner}
                    if source is not None and not source.legacy_oracle_identity
                    else {}
                ),
                "amount": amount,
            }
        )
    return rows


def _generic_player_counter_rows(player: PlayerState) -> dict[str, int]:
    return {
        name: amount
        for name, amount in player_counter_snapshot(player).items()
        if name not in {"poison", "energy"}
    }


@dataclass(slots=True)
class ProjectionCursor:
    """Per-principal protocol memory.

    The cursor belongs to the delivery layer, not the rules engine. A network
    client can persist these three values as its resume token.
    """

    event_id: int = 0
    snapshot: dict[str, Any] | None = None
    seen_oracles: set[str] = field(default_factory=set)
    packet_no: int = 0
    view_hash: str | None = None


class StateProjector:
    """Build small, permission-aware LLM/client views of authoritative state."""

    def __init__(
        self,
        card_db: CardDatabase,
        state: GameState,
        *,
        characteristic_resolver: (
            Callable[[CardInstance], Mapping[str, Any]] | None
        ) = None,
        action_explanation_resolver: (
            Callable[[str], Mapping[str, Mapping[str, Any]]] | None
        ) = None,
    ):
        self.card_db = card_db
        self.state = state
        self.characteristic_resolver = characteristic_resolver
        self.action_explanation_resolver = action_explanation_resolver

    @staticmethod
    def seat_for(principal: str) -> str | None:
        return principal.split(":", 1)[1] if principal.startswith("pilot:") else None

    def _view_seats_for(self, principal: str) -> set[str]:
        """Return every seat whose private information this principal may see.

        A pilot always remains the player for their authenticated seat.  If a
        current capability authorizes that pilot to make decisions for a
        controlled player, CR 723.4 additionally exposes that controlled
        player's information; it does not replace access to the controller's
        own hand or other private information (CR 723.8).
        """

        seats: set[str] = set()
        own_seat = self.seat_for(principal)
        if own_seat in self.state.players:
            seats.add(own_seat)
            seats.update(
                player_seat
                for player_seat, player in self.state.players.items()
                if player.stats.get("turn_controlled_by") == own_seat
            )
        decision = self.state.pending_decision
        if decision is not None:
            capability = next(
                (
                    value
                    for value in self.state.capabilities.values()
                    if value.decision_id == decision.decision_id
                    and value.principal == principal
                    and not value.consumed
                ),
                None,
            )
            if (
                capability is not None
                and capability.actor in self.state.players
            ):
                seats.add(capability.actor)
        return seats

    def _event_visible(self, event: Event, principal: str) -> bool:
        if principal in {"analyst", "admin"}:
            return True
        seats = self._view_seats_for(principal)
        if not event.visibility:
            return True
        if principal == "spectator":
            # Historical public events sometimes enumerate every player seat
            # instead of naming the spectator principal explicitly.  Treat
            # only all-seat visibility as public; a proper subset remains
            # private to those seats.
            return "spectator" in event.visibility or set(
                self.state.players
            ).issubset(event.visibility)
        return principal in event.visibility or any(
            seat in event.visibility for seat in seats
        )

    def _card_visible(self, card: CardInstance, principal: str) -> bool:
        if principal in {"analyst", "admin"}:
            return True
        if card.annotations.get("hidden_after_owner_left"):
            seats = self._view_seats_for(principal)
            return any(
                seat == card.owner
                or seat in card.known_to
                or seat in card.revealed_to
                for seat in seats
            )
        if card.zone in {
            "battlefield",
            "graveyard",
            "exile",
            "command",
            "stack",
        }:
            if not card.face_down:
                return True
            seats = self._view_seats_for(principal)
            return any(
                seat == card.controller
                or seat in card.known_to
                or seat in card.revealed_to
                for seat in seats
            )
        seats = self._view_seats_for(principal)
        return any(
            seat == card.owner
            or seat in card.known_to
            or seat in card.revealed_to
            for seat in seats
        )

    def _effective(self, card: CardInstance) -> dict[str, Any]:
        try:
            record = self.card_db.by_oracle_id(card.oracle_id)
            face = next(
                (
                    (index, value)
                    for index, value in enumerate(record.faces)
                    if card.active_face
                    and str(value.get("name") or "") == card.active_face
                ),
                None,
            )
            face_data = face[1] if face is not None else None
            data: dict[str, Any] = {
                "n": (
                    str(face_data.get("name"))
                    if face_data is not None
                    else record.name
                ),
                "m": (
                    str(face_data.get("mana_cost") or "")
                    if face_data is not None
                    else record.mana_cost
                ),
                "mv": record.mana_value,
                "t": (
                    str(face_data.get("type_line") or "")
                    if face_data is not None
                    else record.type_line
                ),
                "o": (
                    str(face_data.get("oracle_text") or "")
                    if face_data is not None
                    else record.oracle_text
                ),
                "p": face_data.get("power") if face_data is not None else record.power,
                "q": face_data.get("toughness") if face_data is not None else record.toughness,
                "k": list(record.keywords),
                "colors": list(
                    face_data.get("colors") or record.colors
                    if face_data is not None
                    else record.colors
                ),
            }
            if face is not None:
                data["face"] = face[0]
        except KeyError:
            token = (
                card.annotations.get("object_characteristics")
                or card.annotations.get("token_characteristics")
                or {}
            )
            data = {
                "n": card.printed_name,
                "m": token.get("mana_cost", ""),
                "mv": token.get("mana_value", 0),
                "t": token.get("type_line", "Token"),
                "o": token.get(
                    "display_text", token.get("oracle_text", "")
                ),
                "p": token.get("power"),
                "q": token.get("toughness"),
                "k": list(token.get("keywords") or []),
                "colors": list(token.get("colors") or []),
            }
        base = {
            "name": data["n"],
            "mana_cost": data["m"],
            "mana_value": data["mv"],
            "type_line": data["t"],
            "oracle_text": data["o"],
            "power": data["p"],
            "toughness": data["q"],
            "keywords": data["k"],
            "colors": data.pop("colors", []),
        }
        evaluated = (
            dict(self.characteristic_resolver(card))
            if self.characteristic_resolver is not None
            else evaluate_card_characteristics(
                card,
                base,
                runtime_effects=active_resolution_effects(
                    self.state, card
                ),
            )
        )
        characteristic_override = any(
            evaluated.get(field) != base.get(field)
            for field in (
                "name",
                "mana_cost",
                "mana_value",
                "type_line",
                "oracle_text",
                "power",
                "toughness",
                "keywords",
            )
        )
        data.update(
            {
                "n": evaluated["name"],
                "m": evaluated["mana_cost"],
                "mv": evaluated["mana_value"],
                "t": evaluated["type_line"],
                "o": evaluated.get(
                    "display_oracle_text", evaluated["oracle_text"]
                ),
                "p": evaluated.get("power"),
                "q": evaluated.get("toughness"),
                "k": list(evaluated.get("keywords") or []),
                "_characteristic_override": characteristic_override,
            }
        )
        return data

    def _obj(self, card: CardInstance, principal: str) -> dict[str, Any]:
        visible = self._card_visible(card, principal)
        obj: dict[str, Any] = {"id": card.ref}
        if visible and card.object_kind == "emblem":
            obj["n"] = str(
                card.annotations.get("display_label") or "Emblem"
            )
            obj["kind"] = "emblem"
        elif visible:
            obj["cid"] = card.oracle_id[:8]
            effective = self._effective(card)
            if card.face_down:
                try:
                    record = self.card_db.by_oracle_id(card.oracle_id)
                    obj["n"] = record.name
                except KeyError:
                    obj["n"] = card.printed_name
            else:
                obj["n"] = effective["n"]
            characteristic_override = bool(
                effective.get("_characteristic_override")
                or active_resolution_effects(self.state, card)
                or card.annotations.get("copy_overrides")
                or card.annotations.get("continuous_add_types")
                or card.annotations.get("continuous_add_subtypes")
                or card.annotations.get("until_end_of_turn")
                or card.temporary_keywords
            )
            if card.active_face or characteristic_override:
                obj["m"] = effective.get("m", "")
                obj["t"] = effective.get("t", "")
                obj["o"] = truncate(
                    str(effective.get("o") or "").replace("\n", " / "),
                    520,
                )
                if effective.get("p") is not None:
                    obj["p"] = effective["p"]
                if effective.get("q") is not None:
                    obj["q"] = effective["q"]
                if effective.get("k"):
                    obj["k"] = list(effective["k"])
                if effective.get("face") is not None:
                    obj["face"] = effective["face"]
        else:
            obj["n"] = "?"
        if card.tapped:
            obj["tap"] = 1
        if card.face_down:
            obj["fd"] = 1
        if card.counters:
            obj["ctr"] = dict(card.counters)
        if card.marked_damage:
            obj["dmg"] = card.marked_damage
        if card.regeneration_shields:
            obj["regen"] = card.regeneration_shields
        if card.is_token:
            obj["tok"] = 1
        if card.is_commander:
            obj["cmd"] = 1
            if visible and card.commander_designation_id is not None:
                obj["cmd_id"] = card.commander_designation_id
        if card.controller != card.owner:
            obj["ctl"] = card.controller
        if card.attached_to:
            obj["at"] = (
                attachment_target_ref(
                    self.state.cards,
                    self.state.players,
                    card,
                )
                or card.attached_to
            )
        if card.attacking:
            obj["atk"] = card.attacking
        if card.goaded_by:
            obj["goad"] = sorted(
                designation.player for designation in card.goaded_by
            )
        if card.monstrous_value is not None:
            obj["monstrous"] = card.monstrous_value
        if card.renowned:
            obj["renowned"] = True
        if card.unearthed:
            obj["unearthed"] = True
        if card.battle_protector:
            obj["protect"] = card.battle_protector
        return obj

    def _zone(
        self,
        object_ids: Iterable[str],
        principal: str,
        *,
        public_unordered: bool = False,
    ) -> list[dict[str, Any]]:
        identities = list(object_ids)
        if public_unordered and principal not in {"analyst", "admin"}:
            identities.sort(key=lambda object_id: self.state.cards[object_id].ref)
        return [self._obj(self.state.cards[oid], principal) for oid in identities]

    def _decision(self, principal: str) -> dict[str, Any] | None:
        decision = self.state.pending_decision
        if decision is None:
            return None
        capability = next(
            (
                cap for cap in self.state.capabilities.values()
                if cap.decision_id == decision.decision_id
                and cap.principal == principal
                and not cap.consumed
            ),
            None,
        )
        if capability is None:
            return None
        actor_key = capability.actor or principal
        context = copy.deepcopy(decision.payload_by_actor.get(actor_key, {}))
        raw_actions = list(
            (context.get("legal") or {}).get("actions")
            or context.get("legal_actions")
            or (
                {"id": action, "action": action}
                for action in capability.allowed_actions
            )
        )
        legal_actions: list[dict[str, Any]] = []
        for raw_action in raw_actions:
            if not isinstance(raw_action, Mapping):
                continue
            action = copy.deepcopy(dict(raw_action))
            form = build_action_form(
                action,
                decision_kind=decision.kind,
                context=context,
            )
            if form is not None:
                action["form"] = form
            legal_actions.append(action)
        return {
            "cap": capability.token,
            "id": decision.decision_id,
            "kind": decision.kind,
            "actor": capability.actor,
            "allow": list(capability.allowed_actions),
            "legal_actions": legal_actions,
            "sim": 1 if decision.simultaneous else 0,
            "ctx": context,
        }

    def _turn_snapshot(self) -> dict[str, Any]:
        return {
            "seq": self.state.turn_sequence,
            "active": self.state.active_player,
            "phase": self.state.phase,
            "step": self.state.step,
            "priority": self.state.priority_player,
            "passes": list(self.state.priority_passes),
            "extra_q": [
                entry.player for entry in reversed(self.state.extra_turns)
            ],
        }

    def _stack_snapshot(self, principal: str) -> list[dict[str, Any]]:
        stack: list[dict[str, Any]] = []
        for item in reversed(self.state.stack):
            card = (
                self.state.cards.get(item.card_object_id)
                if item.card_object_id
                else None
            )
            hidden_face_down = bool(
                card is not None
                and card.face_down
                and not self._card_visible(card, principal)
            )
            row = {
                "id": item.ref,
                "kind": item.kind,
                "ctl": item.controller,
                "label": "Face-down spell" if hidden_face_down else item.label,
                **({"targets": item.targets} if item.targets else {}),
            }
            if card is not None:
                row.update(
                    {
                        key: value
                        for key, value in self._obj(card, principal).items()
                        if key != "id"
                    }
                )
            stack.append(row)
        return stack

    def _snapshot(self, principal: str) -> dict[str, Any]:
        view_seats = self._view_seats_for(principal)
        players: dict[str, Any] = {}
        for player_seat in self.state.turn_order:
            p = self.state.players[player_seat]
            summary: dict[str, Any] = {
                "life": p.life,
                "poison": p.poison,
                "energy": p.energy,
                "in": 1 if p.in_game else 0,
                "hand_n": len(p.zones["hand"]),
                "lib_n": len(p.zones["library"]),
                "mana": {k: v for k, v in p.mana_pool.items() if v},
                "lands": p.land_plays_remaining,
                "bf": self._zone(p.zones["battlefield"], principal),
                "gy": self._zone(p.zones["graveyard"], principal),
                "ex": self._zone(
                    p.zones["exile"],
                    principal,
                    public_unordered=True,
                ),
                "cmd": self._zone(p.zones["command"], principal),
            }
            generic_counters = _generic_player_counter_rows(p)
            if generic_counters:
                summary[PLAYER_COUNTERS_FIELD] = generic_counters
            commander_damage = _commander_damage_rows(
                self.state, p.commander_damage_received
            )
            if commander_damage:
                summary["cmd_dmg"] = commander_damage
            restricted_mana = p.stats.get("restricted_mana")
            if restricted_mana:
                summary["restricted_mana"] = restricted_mana
            if not p.in_game:
                publicly_known_left = [
                    card
                    for card in self.state.cards.values()
                    if card.owner == player_seat
                    and card.zone == "outside"
                    and self._card_visible(card, principal)
                ]
                if publicly_known_left:
                    summary["left"] = [
                        self._obj(card, principal)
                        for card in sorted(
                            publicly_known_left, key=lambda value: value.ref
                        )
                    ]
            if (
                player_seat in view_seats
                or principal in {"analyst", "admin"}
            ):
                summary["hand"] = self._zone(p.zones["hand"], principal)
            elif view_seats:
                known = [
                    self.state.cards[oid] for oid in p.zones["hand"]
                    if any(
                        seat in self.state.cards[oid].known_to
                        or seat in self.state.cards[oid].revealed_to
                        for seat in view_seats
                    )
                ]
                if known:
                    summary["known_hand"] = [self._obj(card, principal) for card in known]
            known_top = []
            if view_seats:
                for object_id in reversed(p.zones["library"]):
                    card = self.state.cards[object_id]
                    if not any(
                        seat in card.known_to
                        or seat in card.revealed_to
                        for seat in view_seats
                    ):
                        break
                    known_top.append(card)
            if known_top:
                summary["known_top"] = [
                    self._obj(card, principal) for card in known_top[:5]
                ]
            players[player_seat] = summary

        turn = self._turn_snapshot()
        stack = self._stack_snapshot(principal)
        combat = {
            "atk": {
                self.state.cards[oid].ref: defender
                for oid, defender in self.state.combat.attackers.items()
                if oid in self.state.cards
            },
            "blk": {
                self.state.cards[attacker].ref: [self.state.cards[bid].ref for bid in blockers]
                for attacker, blockers in self.state.combat.blockers.items()
                if attacker in self.state.cards
            },
            "damage_step": (
                self.state.combat.damage_step_index + 1
                if (
                    self.state.phase,
                    self.state.step,
                ) == ("combat", "combat_damage")
                else 0
            ),
            "first_strike_step": (
                1 if self.state.combat.first_strike_step else 0
            ),
        }
        snapshot = {
            "rev": self.state.revision,
            "event": self.state.event_sequence,
            "game": {
                "id": self.state.game_id,
                "over": self.state.game_over,
                "winner": self.state.winner,
                "monarch": self.state.monarch,
            },
            "turn": turn,
            "players": players,
            "stack": stack,
            "combat": combat,
        }
        own_seat = self.seat_for(principal)
        if (
            own_seat in view_seats
            and self.action_explanation_resolver is not None
        ):
            explanations = self.action_explanation_resolver(own_seat)
            if explanations:
                snapshot["action_explanations"] = copy.deepcopy(
                    dict(explanations)
                )
        return snapshot

    def _visible_oracles(self, snapshot: Mapping[str, Any]) -> set[str]:
        found: set[str] = set()
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                cid = value.get("cid")
                if cid:
                    # Resolve prefix against state because Oracle IDs are UUIDs.
                    for card in self.state.cards.values():
                        if card.oracle_id.startswith(str(cid)):
                            found.add(card.oracle_id)
                            break
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(snapshot)
        return found

    def _definition(self, oracle_id: str) -> dict[str, Any]:
        try:
            record = self.card_db.by_oracle_id(oracle_id)
            definition: dict[str, Any] = {
                "cid": oracle_id[:8],
                "n": record.name,
                "m": record.mana_cost,
                "mv": record.mana_value,
                "t": record.type_line,
                "o": truncate(record.oracle_text.replace("\n", " / "), 520),
                **({"p": record.power, "q": record.toughness} if record.power is not None else {}),
                **({"k": list(record.keywords)} if record.keywords else {}),
            }
            if record.faces:
                definition["faces"] = [
                    {
                        "n": str(face.get("name") or record.name),
                        "m": str(face.get("mana_cost") or ""),
                        "t": str(face.get("type_line") or ""),
                        "o": truncate(
                            str(face.get("oracle_text") or "").replace(
                                "\n", " / "
                            ),
                            520,
                        ),
                    }
                    for face in record.faces
                ]
            return definition
        except KeyError:
            return {"cid": oracle_id[:8], "n": "Custom token"}

    def _events(self, principal: str, after: int) -> list[dict[str, Any]]:
        result = []
        for event in self.state.events:
            if event.event_id <= after or not self._event_visible(event, principal):
                continue
            if event.importance <= 0 and event.code not in {"decision.response", "action.rejected"}:
                continue
            result.append({
                "id": event.event_id,
                "c": event.code,
                "a": event.actor,
                "s": event.summary,
                **({"d": event.details} if event.importance >= 3 else {}),
            })
        return result[-24:]

    def event_page(
        self,
        principal: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a compact, visibility-filtered event page.

        This deliberately excludes raw event details.  The browser log is a
        public narrative, not an alternate route to authoritative or private
        state.
        """

        if after < 0:
            raise ValueError("after must not be negative")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        visible = [
            event
            for event in self.state.events
            if event.event_id > after
            and self._event_visible(event, principal)
        ]
        page = visible[:limit]
        next_after = (
            page[-1].event_id if page else self.state.event_sequence
        )
        return {
            "events": [
                {
                    "id": event.event_id,
                    "code": event.code,
                    "actor": event.actor,
                    "summary": event.summary,
                    "importance": event.importance,
                }
                for event in page
            ],
            "next_after": next_after,
            "has_more": len(visible) > len(page),
        }

    def packet(
        self,
        principal: str,
        cursor: ProjectionCursor,
        *,
        force_full: bool = False,
    ) -> dict[str, Any]:
        snapshot = self._snapshot(principal)
        current_view_hash = view_hash(snapshot)
        full = force_full or cursor.snapshot is None or cursor.packet_no == 0
        if full:
            payload: dict[str, Any] = {
                "v": PROTOCOL_VERSION,
                "mode": "full",
                "principal": principal,
                "base": None,
                "view": current_view_hash,
                "state": copy.deepcopy(snapshot),
            }
        else:
            payload = {
                "v": PROTOCOL_VERSION,
                "mode": "delta",
                "principal": principal,
                "base": cursor.view_hash,
                "view": current_view_hash,
                "rev": snapshot["rev"],
                "event": snapshot["event"],
                "patch": json_patch(cursor.snapshot, snapshot),
            }

        # Decision capabilities are delivery metadata rather than persistent
        # view state. Repeat the live capability until it is consumed, and send
        # null explicitly so a client clears a stale decision after a delta.
        payload["decision"] = self._decision(principal)
        payload["view_revision"] = snapshot["rev"]

        visible = self._visible_oracles(snapshot)
        new_oracles = sorted(visible - cursor.seen_oracles)
        if new_oracles:
            payload["defs"] = [self._definition(oracle_id) for oracle_id in new_oracles]
        events = self._events(principal, cursor.event_id)
        if events:
            payload["events"] = events

        cursor.snapshot = copy.deepcopy(snapshot)
        cursor.view_hash = current_view_hash
        cursor.event_id = self.state.event_sequence
        cursor.seen_oracles.update(visible)
        cursor.packet_no += 1
        payload["pkt"] = cursor.packet_no
        return payload

    @staticmethod
    def measure(packet: Mapping[str, Any]) -> dict[str, int]:
        compact = json.dumps(packet, separators=(",", ":"), ensure_ascii=False)
        pretty = stable_json(packet)
        return {
            "compact_chars": len(compact),
            "compact_bytes": len(compact.encode("utf-8")),
            "pretty_chars": len(pretty),
            "estimated_tokens": max(1, len(compact) // 4),
        }
