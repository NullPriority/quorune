from __future__ import annotations

from bisect import bisect_right
import copy
import json
from dataclasses import dataclass, field
from itertools import islice
from typing import Any, Callable, Iterable, Mapping, Sequence

from .announced_damage import ANNOUNCED_DAMAGE_CONTEXT
from .carddb import CardDatabase, CardRecord
from .characteristic_evaluation import evaluate_card_characteristics
from .choice_forms import build_action_form
from .permissions import CapabilityManager, current_turn_controller, decision_context_for_capability
from .attachments import attachment_target_ref
from .commander import commander_damage_source
from .commander_cast_counts import commander_cast_count
from .continuous_effect_state import active_resolution_effects, is_fixed_target_color_effect
from .counter_state import player_counter_snapshot
from .omen import omen_characteristic_face
from .rules.action_catalog import visible_decision_payload
from .rules.face_characteristic_values import current_face_mana_value
from .model import (
    CardInstance,
    Event,
    StackItem,
    GameState,
    PlayerState,
    PLAYER_COUNTERS_FIELD,
)
from .object_query import exact_numeric_characteristic
from .protocol import PROTOCOL_VERSION, json_patch, view_hash
from .turn_priority_model import upcoming_turn_player
from .util import stable_json, truncate
from .zone_object_state import face_down_exile_viewers


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
    observation_epoch: object | None = field(default=None, repr=False)
    observation_principal: str | None = field(default=None, repr=False)


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
        library_top_visibility_resolver: (
            Callable[[str, str | None], bool] | None
        ) = None,
        land_play_count_resolver: Callable[[str], int] | None = None,
        hand_visibility_resolver: Callable[[str], bool] | None = None,
        goading_players_resolver: Callable[[CardInstance], tuple[str, ...]] | None = None,
        stack_effects_resolver: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
        land_entry_life_resolver: Callable[[CardRecord], int] | None = None,
    ):
        self.card_db = card_db
        self.state = state
        self.characteristic_resolver = characteristic_resolver
        self.action_explanation_resolver = action_explanation_resolver
        self.library_top_visibility_resolver = (
            library_top_visibility_resolver
        )
        self.land_play_count_resolver = land_play_count_resolver
        self.hand_visibility_resolver = hand_visibility_resolver
        self.goading_players_resolver = goading_players_resolver
        self.stack_effects_resolver = stack_effects_resolver
        self.land_entry_life_resolver = land_entry_life_resolver

    @staticmethod
    def seat_for(principal: str) -> str | None:
        return principal.split(":", 1)[1] if principal.startswith("pilot:") else None

    def _view_seats_for(self, principal: str, *, include_search_scope: bool = True) -> set[str]:
        """Return every seat whose private information this principal may see.

        A pilot keeps access for their authenticated seat while controlling
        another player. The live grant also exposes the controlled player's
        information, including between that player's decisions (CR 723.4, 723.8).
        """

        seats: set[str] = set()
        own_seat = self.seat_for(principal)
        if own_seat in self.state.players:
            seats.add(own_seat)
            seats.update(
                player_seat
                for player_seat in self.state.players
                if current_turn_controller(self.state, player_seat) == own_seat
            )
        capability = CapabilityManager(self.state).capability_for(principal)
        if capability is not None and capability.actor in self.state.players:
            from .library_search_control import pending_library_search_control

            if (include_search_scope or pending_library_search_control(
                    self.state, self.state.pending_decision, capability.actor) is None):
                seats.add(capability.actor)
        return seats

    def _event_visible(self, event: Event, principal: str, seats: set[str] | None = None) -> bool:
        if principal in {"analyst", "admin"}:
            return True
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
        if seats is None:
            seats = self._view_seats_for(principal, include_search_scope=False)
        return principal in event.visibility or any(
            seat in event.visibility for seat in seats
        )

    def _card_visible(self, card: CardInstance, principal: str) -> bool:
        if principal in {"analyst", "admin"}:
            return True
        if card.zone == "exile" and card.face_down:
            return bool(self._view_seats_for(principal) & face_down_exile_viewers(self.state, card))
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
        ) or (card.zone == 'hand' and (
            self._hand_is_public(card.owner) or self._scoped_hand_card_is_revealed(card)
        ))

    def _hand_is_public(self, owner: str) -> bool:
        return self.hand_visibility_resolver is not None and self.hand_visibility_resolver(owner)

    def _scoped_hand_card_is_revealed(self, card: CardInstance) -> bool:
        from .drawing.captured_hand import captured_draw_is_public
        from .drawing.miracle_triggers import miracle_reveal_active
        return miracle_reveal_active(self.state, card) or captured_draw_is_public(self.state, card)

    def _effective(self, card: CardInstance) -> dict[str, Any]:
        try:
            record = self.card_db.by_oracle_id(card.oracle_id)
            omen_face = omen_characteristic_face(
                card, record, cast_face_version=getattr(self.state, "cast_face_offers_version", None),
            )
            face_name = omen_face["name"] if omen_face is not None else card.active_face
            face = next(
                (
                    (index, value)
                    for index, value in enumerate(record.faces)
                    if face_name and str(value.get("name") or "") == face_name
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
                "mv": current_face_mana_value(record, omen_face) if omen_face is not None else record.mana_value,
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
        if card.shared_type_doors is not None and not card.face_down:
            from .carddb_characteristics import base_card_characteristics
            from .card_program_faces import current_shared_type_doors
            doors = current_shared_type_doors(card)
            if doors is not None:
                base = base_card_characteristics(card, self.card_db.by_oracle_id(card.oracle_id))
                data.pop("face", None)
                data["room_doors"] = {"locked": list(doors.locked), "unlocked": list(doors.unlocked)}
        evaluated = (
            dict(self.characteristic_resolver(card))
            if self.characteristic_resolver is not None
            else evaluate_card_characteristics(
                card,
                base,
                runtime_effects=active_resolution_effects(
                    self.state, card
                ),
                dependency_order_version=getattr(self.state, 'continuous_dependency_order_version', None),
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
                "colors": list(evaluated.get("colors") or []),
                "_characteristic_override": characteristic_override,
            }
        )
        return data

    def _obj(
        self, card: CardInstance, principal: str, *, public_hand: bool = False, known_library_top: bool = False,
    ) -> dict[str, Any]:
        visible = (
            (public_hand and card.zone == "hand")
            or (known_library_top and card.zone == "library")
            or self._card_visible(card, principal)
        )
        public_characteristics = card.zone in {"battlefield", "stack"}
        resolution_effects = active_resolution_effects(self.state, card) if visible or public_characteristics else ()
        effective = None
        obj: dict[str, Any] = {"id": card.ref}
        if visible and card.object_kind == "emblem":
            obj["n"] = str(
                card.annotations.get("display_label") or "Emblem"
            )
            obj["kind"] = "emblem"
        elif visible:
            obj["cid"] = card.oracle_id[:8]
            if card.printing is not None:
                obj["printing"] = card.printing.to_dict()
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
                or resolution_effects
                or card.annotations.get("copy_overrides")
                or card.annotations.get("continuous_add_types")
                or card.annotations.get("continuous_add_subtypes")
                or card.annotations.get("until_end_of_turn")
                or card.temporary_keywords
            )
            if "room_doors" in effective:
                obj["room_doors"] = effective["room_doors"]
            if card.active_face or effective.get("face") is not None or characteristic_override or "room_doors" in effective:
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
            if card.zone == "battlefield" and not card.face_down:
                characteristics = {
                    "type_line": effective["t"],
                    "power": effective["p"],
                    "toughness": effective["q"],
                }
                for key, stat in (("p", "power"), ("q", "toughness")):
                    current = exact_numeric_characteristic(card, characteristics, stat)
                    if current is not None:
                        obj[key] = current
        else:
            obj["n"] = "?"
        if public_characteristics and any(is_fixed_target_color_effect(effect) for effect in resolution_effects):
            obj["colors"] = (effective if effective is not None else self._effective(card))["colors"]
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
            if card.zone == "command":
                tax = 2 * commander_cast_count(self.state, card.owner, card.oracle_id)
                if tax:
                    obj["cmd_tax"] = tax
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
        goaders = (
            self.goading_players_resolver(card)
            if self.goading_players_resolver is not None and card.zone == "battlefield" and card.attachments
            else tuple(designation.player for designation in card.goaded_by)
        )
        if goaders:
            obj["goad"] = sorted(goaders)
        if card.monstrous_value is not None:
            obj["monstrous"] = card.monstrous_value
        if card.suspected_at is not None:
            obj["suspected"] = True
        if card.renowned:
            obj["renowned"] = True
        from .saddle import is_saddled
        if is_saddled(card):
            obj["saddled"] = True
        if card.zone == 'battlefield' and not card.phased_out and getattr(self.state, 'soulbond_pairs', None):
            from .soulbond_state import paired_object_identity
            partner = paired_object_identity(self.state, card)
            if partner is not None:
                obj['paired_with'] = self.state.cards[partner.object_id].ref
        if card.zone=='battlefield' and 'prepared_designation' in card.annotations:
            from .preparation import prepared_designation
            obj['prepared']=prepared_designation(card) is not None
        if card.class_level is not None:
            obj["class_level"] = card.class_level
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
        public_hand: bool = False,
    ) -> list[dict[str, Any]]:
        identities = list(object_ids)
        if public_unordered and principal not in {"analyst", "admin"}:
            identities.sort(key=lambda object_id: self.state.cards[object_id].ref)
        return [self._obj(self.state.cards[oid], principal, public_hand=public_hand) for oid in identities]

    def _decision(self, principal: str) -> dict[str, Any] | None:
        decision = self.state.pending_decision
        if decision is None:
            return None
        capability = CapabilityManager(self.state).capability_for(principal)
        if capability is None:
            return None
        actor_key = capability.actor or principal
        context = copy.deepcopy(decision_context_for_capability(decision, capability))
        self._present_resolution_choice(decision, context, principal)
        if decision.kind == "trigger.order":
            self._present_trigger_order(decision, context, capability.actor)
        from .rules.mana_pool_payment import mana_pool_choice_context

        context = mana_pool_choice_context(self.state, capability.actor, decision.kind, context)
        from .land_entry_coordination import offer_land_entry_payment_recovery
        offer_land_entry_payment_recovery(self.state, decision, capability.actor, context)
        if principal not in {"analyst", "admin"}:
            context = visible_decision_payload(self.state, actor_key, context)
        if decision.kind == "replacement.order":
            from .commander_zones import present_commander_replacement_options

            present_commander_replacement_options(context, decision.continuation)
        labels: dict[str, str] = {}
        if decision.kind == "state.commander_zone" and context.get("origin") in {"graveyard", "exile"}:
            origin = context["origin"]
            commander = next((card for card in self.state.cards.values()
                              if card.ref == context.get("commander")), None)
            name = (
                commander.printed_name if commander is not None and self._card_visible(commander, principal)
                else "your commander"
            )
            context["prompt"] = f"Move {name} to the command zone?"
            labels = {"command": "Move to command zone", "remain": f"Leave in {'the ' if origin == 'graveyard' else ''}{origin}"}
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
            action_id = action.get("id")
            if isinstance(action_id, str) and action_id in labels:
                action["label"] = labels[action_id]
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

    def _present_resolution_choice(self, decision, context, principal):
        """Describe the current effect and the cards offered to this principal."""
        operation = context.get("operation")
        is_search = decision.kind == "semantic.search" and operation == "search"
        is_fetch = decision.kind == "search.fetch"
        if not (is_search or is_fetch) and operation not in {"offer_optional_effect", "offer_draw", "scry"}:
            return
        item = next((row for row in self.state.stack if row.ref == context.get("stack")), None)
        if item is None:
            return
        source = self.state.cards.get(item.source_object_id or item.card_object_id)
        if source is not None and self._card_visible(source, principal):
            source_name = self._effective(source)["n"]
        else:
            source_name = "Resolving ability"
        presentation = {"source": source_name}
        if operation == "scry":
            presentation.update(title="Scry", detail="Put the card on the top or bottom of your library.")
        elif operation == "offer_draw":
            one_for_chooser = context.get("count") == 1 and context.get("drawer") in decision.actors
            presentation.update(title="Draw a card?" if one_for_chooser else str(context["prompt"]), accept="Draw")
        elif is_search or is_fetch:
            effect = (decision.continuation.get("selection") or {}).get("payload", {}).get("effect", {})
            spec = context.get("search_spec") or {}
            if effect and spec:
                presentation.update(_search_presentation(effect, choosing=True))
            if is_fetch:
                presentation.update(
                    title="Choose a land", detail="Put the selected land onto the battlefield, then shuffle.",
                    accept="Choose selected land",
                )
            show_entry_life = is_fetch and not item.context.get("fetch_entry_tapped") and any(
                action.get("choice_schema", {}).get("entry_pay_life") == "boolean"
                for action in context.get("legal_actions", ())
            )
            context["preview_cards"] = self._search_preview_cards(
                context.get("search_cards") or (), show_entry_life=show_entry_life,
            )
        else:
            effects = (decision.continuation.get("effect") or {}).get("effects") or ()
            if len(effects) != 1:
                return
            effect = effects[0]
            if effect.get("op") == "search":
                presentation.update(_search_presentation(effect, choosing=False))
            elif effect.get("op") == "draw" and effect.get("count") == 1:
                presentation.update(title="Draw a card?", accept="Draw")
            else:
                return
        if "title" in presentation:
            context["presentation"] = presentation

    def _present_trigger_order(self, decision: Any, context: dict[str, Any], actor: str | None) -> None:
        continuation = decision.continuation
        batch_id = continuation.get("trigger_batch_id") or continuation.get("semantic_trigger_batch_id")
        for batch in self.state.pending_trigger_batches:
            if batch.batch_id != batch_id or batch.groups[0].controller != actor:
                continue
            offered = {row["id"] for row in context.get("triggers", ())}
            previews = []
            for item in batch.groups[0].items:
                card = self.state.cards.get(item.source_object_id)
                if item.ref in offered and card is not None and self._card_visible(card, "spectator"):
                    previews.append({"id": item.ref, "n": card.printed_name, "cid": card.oracle_id[:8]})
            if previews:
                context["preview_cards"] = previews
            return

    def _search_preview_cards(self, rows, *, show_entry_life: bool) -> list[dict[str, Any]]:
        allowed = {
            row["id"]: row["name"] for row in rows
            if isinstance(row, Mapping) and "id" in row and "name" in row
        }
        previews = []
        entry_costs: dict[str, int] = {}
        for card in self.state.cards.values():
            if card.zone != "library" or card.ref not in allowed or card.face_down:
                continue
            preview = {"id": card.ref, "n": allowed[card.ref], "cid": card.oracle_id[:8]}
            if card.printing:
                preview["printing"] = card.printing.to_dict()
            if show_entry_life and self.land_entry_life_resolver:
                amount = entry_costs.get(card.oracle_id)
                if amount is None:
                    amount = self.land_entry_life_resolver(self.card_db.by_oracle_id(card.oracle_id))
                    entry_costs[card.oracle_id] = amount
                if amount:
                    preview["entry_life"] = amount
            previews.append(preview)
        return previews

    def _turn_snapshot(self) -> dict[str, Any]:
        graveyard_activity = next((
            event.event_id for event in reversed(self.state.events)
            if (event.code == "card.mill" or event.code == "zone.move" and (
                event.details.get("from") == "graveyard" or event.details.get("to") == "graveyard"
            )) and self._event_visible(event, "spectator")
        ), 0)
        return {
            "seq": self.state.turn_sequence,
            "active": self.state.active_player,
            "number": self.state.players[self.state.active_player].turns_begun if self.state.active_player else 0,
            "phase": self.state.phase,
            "step": self.state.step,
            "priority": self.state.priority_player,
            "passes": list(self.state.priority_passes),
            "graveyard_activity": graveyard_activity,
            **({"next_player": upcoming_turn_player(self.state)} if self.state.step == "end_step" else {}),
            **({"fast_empty_priority": True} if self.state.config.auto_pass_empty_priority else {}),
            "extra_q": [
                entry.player for entry in reversed(self.state.extra_turns)
                if self.state.departed_turn_boundary_version is None or self.state.players[entry.player].in_game
            ],
        }

    def _stack_touches_graveyard(self, item: StackItem, card: CardInstance | None) -> bool:
        from .rules.effect_shape_walkers import nested_effects

        source = card if card is not None else self.state.cards.get(item.source_object_id)
        if source is None or not self._card_visible(source, "spectator"):
            return False
        effects = self.stack_effects_resolver(item.semantic_key) if item.semantic_key and self.stack_effects_resolver else ()
        for effect in nested_effects((effects, item.context.get("dynamic_effects"))):
            if effect["op"] in {"mill", "reanimate", "exile_graveyard", "exile_opponent_graveyards"}:
                return True
            if any(effect.get(field) == "graveyard" for field in ("origin", "zone", "from", "destination")):
                return True
            moves = effect.get("set")
            if effect["op"] == "move_public_zone_set" and isinstance(moves, Mapping) and moves.get("origin") == "graveyard":
                return True
        return False

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
                **({"from": "graveyard"} if item.context.get("cast_origin") == "graveyard" else {}),
                **({"graveyard_interaction": True} if self._stack_touches_graveyard(item, card) else {}),
            }
            if not hidden_face_down:
                if item.modes:
                    row["modes"] = list(item.modes)
                if item.x_value is not None:
                    row["x"] = item.x_value
                source = self.state.cards.get(item.source_object_id)
                if card is None and source is not None and self._card_visible(source, principal):
                    visible_source = self._obj(source, principal)
                    row.update({
                        key: visible_source[key] for key in ("cid", "n", "face", "printing")
                        if key in visible_source
                    })
            allocation = item.context.get(ANNOUNCED_DAMAGE_CONTEXT)
            if isinstance(allocation, Mapping) and not hidden_face_down:
                shares = allocation.get("shares")
                if (isinstance(shares, (list, tuple)) and len(shares) == len(item.targets)
                        and all(type(share) is int and share > 0 for share in shares)):
                    row["damage_shares"] = list(shares)
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

    def _known_library_top(
        self,
        *,
        player: Any,
        player_seat: str,
        view_seats: set[str],
    ) -> list[Any]:
        permission_visible = bool(
            self.library_top_visibility_resolver is not None
            and (
                self.library_top_visibility_resolver(player_seat, None)
                or any(
                    self.library_top_visibility_resolver(player_seat, seat)
                    for seat in view_seats
                )
            )
        )
        known = (
            [self.state.cards[player.zones["library"][-1]]]
            if permission_visible and player.zones["library"]
            else []
        )
        if view_seats:
            for object_id in reversed(
                player.zones["library"][
                    : -1 if permission_visible else None
                ]
            ):
                card = self.state.cards[object_id]
                if not any(
                    seat in card.known_to or seat in card.revealed_to
                    for seat in view_seats
                ):
                    break
                known.append(card)
        return known

    def _hand_summary(self, owner: str, principal: str, view_seats: set[str]) -> dict[str, Any]:
        identities = self.state.players[owner].zones['hand']
        if owner in view_seats or principal in {'analyst', 'admin'}:
            return {'hand': self._zone(identities, principal)}
        if self._hand_is_public(owner):
            return {'known_hand': self._zone(identities, principal, public_unordered=True, public_hand=True)}
        if view_seats:
            known = [self.state.cards[oid] for oid in identities if any(
                seat in self.state.cards[oid].known_to or seat in self.state.cards[oid].revealed_to
                for seat in view_seats)]
            if known:
                return {'known_hand': [self._obj(card, principal) for card in known]}
        else:
            revealed = [oid for oid in identities if self._scoped_hand_card_is_revealed(self.state.cards[oid])]
            if revealed:
                return {'known_hand': self._zone(revealed, principal, public_unordered=True)}
        return {}

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
                "lands": (
                    self.land_play_count_resolver(player_seat)
                    if self.land_play_count_resolver is not None
                    else p.land_plays_remaining
                ),
                "bf": self._zone(p.zones["battlefield"], principal),
                "gy": self._zone(p.zones["graveyard"], principal),
                "ex": self._zone(
                    p.zones["exile"],
                    principal,
                    public_unordered=True,
                ),
                "cmd": self._zone(p.zones["command"], principal),
            }
            if p.dungeon_journey is not None:
                summary["dungeon_journey"] = p.dungeon_journey.to_dict()
            generic_counters = _generic_player_counter_rows(p)
            from .city_blessing_model import player_has_citys_blessing
            if player_has_citys_blessing(p):summary['citys_blessing']=True
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
            summary.update(self._hand_summary(player_seat, principal, view_seats))
            known_top = self._known_library_top(
                player=p,
                player_seat=player_seat,
                view_seats=view_seats,
            )
            if known_top:
                summary["known_top"] = [
                    self._obj(card, principal, known_library_top=True) for card in known_top[:5]
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
        if self.state.combat.attack_bands:
            from .banding_model import projected_attack_bands
            combat['bands'] = projected_attack_bands(self.state.combat, self.state.cards)
        snapshot = {
            "rev": self.state.revision,
            "event": self.state.event_sequence,
            "game": {
                "id": self.state.game_id,
                "over": self.state.game_over,
                "winner": self.state.winner,
                "monarch": self.state.monarch,
                **({"initiative": self.state.initiative} if self.state.initiative is not None else {}),
                **(
                    {"day_night": self.state.day_night}
                    if self.state.day_night is not None
                    else {}
                ),
            },
            "turn": turn,
            "players": players,
            "stack": stack,
            "combat": combat,
        }
        pending = self.state.pending_decision
        if pending is not None and pending.kind == "cast.assist" and len(pending.actors) == 1:
            announcement = pending.payload_by_actor.get(pending.actors[0], {}).get("assist_public")
            if isinstance(announcement, Mapping):
                snapshot["assist_cast"] = copy.deepcopy(dict(announcement))
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

    def _visible_oracles(self, *views: Any) -> set[str]:
        prefixes: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                cid = value.get("cid")
                if cid:
                    prefixes.add(str(cid))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for view in views:
            walk(view)
        if not prefixes:
            return set()
        found: set[str] = set()
        lengths = {len(prefix) for prefix in prefixes}
        for card in self.state.cards.values():
            for length in lengths:
                prefix = card.oracle_id[:length]
                if len(prefix) == length and prefix in prefixes:
                    found.add(card.oracle_id)
                    prefixes.remove(prefix)
            if not prefixes:
                break
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
                **({"produced_mana": list(record.produced_mana)} if record.produced_mana and record.is_land else {}),
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

    def _cast_target_label(self, ref: str) -> str:
        if ref in self.state.players:
            return f"Seat {ref}"
        for item in self.state.stack:
            if ref == item.ref:
                card = self.state.cards.get(item.card_object_id)
                return "a face-down spell" if card is not None and card.face_down else item.label
        for card in self.state.cards.values():
            if ref == card.ref:
                if self._public_activity_identity(card):
                    return card.printed_name
                break
        return "an object"

    def _public_activity_identity(self, card: CardInstance) -> bool:
        if self._card_visible(card, "spectator"):
            return True
        # A departed seat's previously public physical cards remain known in
        # the journal. A concealed departure or vanished token reveals nothing.
        return (card.zone == "outside" and not card.face_down and not card.is_token
                and not card.annotations.get("hidden_after_owner_left")
                and set(self.state.players).issubset(card.known_to))

    def _journaled_move_name(self, event: Event, ref: str) -> str:
        origin = event.details.get("from")
        destination = event.details.get("to")
        ending = f": {origin} \u2192 {destination}."
        prefix = f"{ref} "
        if event.code == "zone.move" and event.summary.startswith(prefix) and event.summary.endswith(ending):
            return event.summary[len(prefix):-len(ending)]
        return ""

    def _event_summary(self, event: Event) -> str:
        from .rules.secret_number_choices import REVEAL_EVENT, secret_number_reveal_summary

        if event.code == REVEAL_EVENT:
            return secret_number_reveal_summary(event.details.get("choices")) or event.summary
        if event.code == "stack.cast":
            summary = event.summary.rstrip(".")
            if event.details.get("cost_option") == "without_mana_cost":
                follow = self._subsequent_public_events(event)
                mechanics = {"cascade.resolve": "cascade", "suspend.cast.resolve": "suspend"}
                stack_ref = event.details.get("stack")
                mechanic = next((mechanics[row.code] for row in follow
                                 if row.code in mechanics and row.details.get("cast_stack") == stack_ref), None)
                if mechanic:
                    summary += f" from {mechanic}"
            if event.details.get("targets"):
                targets = ", ".join(self._cast_target_label(ref) for ref in event.details["targets"][:8])
                if len(event.details["targets"]) > 8:
                    targets += f" and {len(event.details['targets']) - 8} more"
                summary += f" targeting {targets}"
            return summary + "."
        if event.code == "card.suspend" and type(event.details.get("card")) is str:
            card = self._cast_target_label(event.details["card"])
            counters = event.details.get("time_counters")
            if type(counters) is int:
                return f"Suspended {card} with {counters} time {'counter' if counters == 1 else 'counters'}."
        if event.code == "stack.trigger" and event.summary.endswith(" — Cascade."):
            source = event.summary.partition(": ")[2].removesuffix(" — Cascade.")
            if source:
                return f"Cascade triggered for {source}."
        if event.code in {"cascade.resolve", "suspend.cast.resolve"} and event.details.get("outcome") == "cast":
            card = event.details.get("candidate")
            if isinstance(card, str):
                mechanic = "cascade" if event.code == "cascade.resolve" else "suspend"
                return f"Cast {self._cast_target_label(card)} from {mechanic}."
        if event.code == "state.commander_zone" and type(event.details.get("commander")) is str:
            card = self._cast_target_label(event.details["commander"])
            origin = event.details.get("origin")
            zone = "the graveyard" if origin == "graveyard" else "exile"
            if event.details.get("choice") == "command":
                return f"{card} moved from {zone} to the command zone."
            return f"{card} stayed in {zone}."
        if event.code in {"zone.move", "permanent.destroyed", "state.creatures_died", "choice.sacrifice", "cost.sacrifice"}:
            return self._zone_event_summary(event)
        if event.code == "combat.damage":
            damage = self._combat_event_summary(event)
            if damage:
                return damage
        if (
            event.summary == event.code == "library.top.reveal"
            and set(event.details) == {"player", "card"}
        ):
            player, card = event.details["player"], event.details["card"]
            if type(player) is str and player == event.actor and type(card) is str and card:
                return f"{player} revealed {card}."
        return event.summary

    def _zone_event_summary(self, event: Event) -> str:
        details = event.details
        ref = details.get("object")
        name = self._cast_target_label(ref) if isinstance(ref, str) else "A card"
        zone_names = {"battlefield": "the battlefield", "graveyard": "the graveyard",
                      "library": "the library", "hand": "its owner's hand", "exile": "exile",
                      "command": "the command zone", "stack": "the stack"}
        destination = details.get("destination") or details.get("to")
        zone = zone_names.get(destination)
        if event.code == "zone.move":
            origin = zone_names.get(details.get("from"))
            if not origin or not zone:
                return event.summary
            # This journal entry can name a card publicly even when the card is
            # no longer visible in the current projection.
            if name == "an object" and isinstance(ref, str):
                name = self._journaled_move_name(event, ref) or name
            cost = details.get("reason")
            if origin == "the battlefield" and isinstance(cost, str):
                if cost.endswith(" sacrifice cost"):
                    return f"{name} was sacrificed as a casting cost and went to {zone}."
                if cost == "activated ability cost":
                    return f"{name} moved from {origin} to {zone} to pay for an ability."
            return f"{name} moved from {origin} to {zone}."
        if event.code == "permanent.destroyed":
            reason = details.get("reason")
            cause = ""
            if details.get("cause") == "state_based_action":
                hits = self._combat_hits_before(event, ref)
                cause = " due to lethal damage or deathtouch"
                if hits:
                    damage = ", ".join(
                        f"{amount} from {self._cast_target_label(source)}"
                        for source, amount in hits[:4]
                    )
                    if len(hits) > 4:
                        damage += f", and {len(hits) - 4} more assignments"
                    cause = f" after taking combat damage ({damage})"
            elif isinstance(reason, str) and self._event_effect_source(event, reason):
                cause = f" by {reason}"
            moved = f" and went to {zone}" if zone else ""
            return f"{name} was destroyed{cause}{moved}."
        if event.code == "cost.sacrifice":
            source = details.get("spell")
            spell = self._cast_target_label(source) if isinstance(source, str) else "an object"
            cost = f" to cast {spell}" if spell != "an object" else " as a casting cost"
            return f"Sacrificed {name}{cost}."
        refs = [value for value in details.get("objects") or () if isinstance(value, str)]
        names = [self._cast_target_label(value) for value in refs[:4]]
        if len(refs) > len(names):
            names.append(f"{len(refs) - len(names)} more permanents")
        if not names:
            return event.summary
        if event.code == "choice.sacrifice":
            source = self._event_effect_source(event)
            reason = f" while {source} resolved" if source else ""
            return f"Sacrificed {', '.join(names)}{reason}."
        # This aggregate historically reports proposed ordinary moves. Use
        # the per-object destruction event for the cause and actual destination.
        return f"State-based actions applied to {', '.join(names)}."

    def _event_effect_source(self, event: Event, expected: str | None = None) -> str:
        # Resolution follows its journaled effects. Attribute only the first
        # public spell resolution in this command, when its name is recorded.
        for row in self._subsequent_public_events(event):
            if row.code != "stack.resolve":
                if expected is None:
                    break
                continue
            words = row.summary.rstrip(".").split(" ", 2)
            if len(words) == 3 and row.details.get("destination") is not None and (expected is None or words[2] == expected):
                return words[2]
            break
        return ""

    def _subsequent_public_events(self, event: Event) -> Iterable[Event]:
        index = bisect_right(self.state.events, event.event_id, key=lambda row: row.event_id)
        for row in islice(self.state.events, index, index + 32):
            if row.revision != event.revision:
                break
            if self._event_visible(row, "spectator"):
                yield row

    def _combat_hits_before(self, event: Event, target: Any) -> list[tuple[str, int]]:
        index = bisect_right(self.state.events, event.event_id - 1, key=lambda row: row.event_id)
        for offset in range(index - 1, max(-1, index - 33), -1):
            row = self.state.events[offset]
            if row.revision != event.revision or row.step != event.step:
                break
            if row.code != "combat.damage" or not self._event_visible(row, "spectator"):
                continue
            return [(hit["source"], hit["amount"]) for hit in row.details.get("damage_events") or ()
                    if isinstance(hit, Mapping) and hit.get("target") == target
                    and isinstance(hit.get("source"), str)
                    and type(hit.get("amount")) is int and hit["amount"] > 0]
        return []

    def _activity_cards(self, event: Event) -> list[dict[str, Any]]:
        if not self._event_visible(event, "spectator"):
            return []
        details = event.details
        if event.code == "combat.damage":
            refs = [ref for hit in (details.get("damage_events") or ())[:3] if isinstance(hit, Mapping)
                    for ref in (hit.get("source"), hit.get("target"))]
        elif event.code == "permanent.destroyed":
            refs = [details.get("object"), *(ref for ref, _ in self._combat_hits_before(event, details.get("object"))[:4])]
        elif event.code in {"state.creatures_died", "choice.sacrifice"}:
            refs = (details.get("objects") or ())[:4]
        elif event.code in {"stack.cast", "stack.activate", "stack.trigger", "zone.move", "land.play",
                            "card.suspend", "state.commander_zone", "cost.sacrifice", "cascade.resolve",
                            "suspend.cast.resolve"}:
            refs = [details.get(key) for key in ("object", "source", "commander", "card", "spell", "candidate")]
            refs += list((details.get("targets") or ())[:4])
        else:
            return []
        ordered = dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref not in self.state.players)
        if not ordered:
            return []
        rows = {}
        for card in self.state.cards.values():
            if card.ref not in ordered:
                continue
            visible = self._public_activity_identity(card)
            named = self._journaled_move_name(event, card.ref) == card.printed_name
            if visible or named:
                rows[card.ref] = {
                    "id": card.ref, "n": card.printed_name, "cid": card.oracle_id[:8],
                    **({"printing": card.printing.to_dict()} if visible and card.printing else {}),
                    **({"tok": 1} if card.is_token else {}),
                }
        return [rows[ref] for ref in ordered if ref in rows]

    def _combat_event_summary(self, event: Event) -> str:
        hits = []
        for damage in event.details.get("damage_events") or ():
            if not isinstance(damage, Mapping) or type(damage.get("amount")) is not int or damage["amount"] <= 0:
                continue
            attacker = damage.get("source")
            receiver = damage.get("player") or damage.get("target")
            if isinstance(attacker, str) and isinstance(receiver, str):
                hits.append((attacker, receiver, damage["amount"]))
        if not hits:
            return ""
        lines = [
            f"{self._cast_target_label(source)} dealt {amount} combat damage to {self._cast_target_label(target)}"
            for source, target, amount in hits[:3]
        ]
        if len(hits) > 3:
            lines.append(f"{len(hits) - 3} more damage assignments")
        return "; ".join(lines) + "."

    def _events(self, principal: str, after: int) -> list[dict[str, Any]]:
        result = []
        seats = self._view_seats_for(principal, include_search_scope=False)
        for event in reversed(self.state.events):
            if event.event_id <= after:
                break
            if not self._event_visible(event, principal, seats):
                continue
            if event.importance <= 0 and event.code not in {"decision.response", "action.rejected"}:
                continue
            cards = self._activity_cards(event)
            result.append({
                "id": event.event_id,
                "c": event.code,
                "a": event.actor,
                "s": self._event_summary(event),
                **({"pub": True} if self._event_visible(event, "spectator") else {}),
                **({"d": event.details} if event.importance >= 3 else {}),
                **({"cards": cards} if cards else {}),
            })
            if len(result) == 24:
                break
        return result[::-1]

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
        events = self.state.events
        start = bisect_right(events, after, key=lambda event: event.event_id)
        seats = None if principal in {"spectator", "analyst", "admin"} else self._view_seats_for(
            principal, include_search_scope=False,
        )
        tail = (events[index] for index in range(start, len(events)))
        visible = list(islice(
            (event for event in tail if self._event_visible(event, principal, seats)),
            limit + 1,
        ))
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
                    "summary": self._event_summary(event),
                    "importance": event.importance,
                    **({"cards": cards} if (cards := self._activity_cards(event)) else {}),
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
        observation_epoch: object | None = None,
    ) -> dict[str, Any]:
        cached = (
            observation_epoch is not None and cursor.observation_epoch is observation_epoch
            and cursor.observation_principal == principal and cursor.snapshot is not None
        )
        snapshot = cursor.snapshot if cached else self._snapshot(principal)
        current_view_hash = cursor.view_hash if cached else view_hash(snapshot)
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
                "patch": [] if cached else json_patch(cursor.snapshot, snapshot),
            }

        # Decision capabilities are delivery metadata rather than persistent
        # view state. Repeat the live capability until it is consumed, and send
        # null explicitly so a client clears a stale decision after a delta.
        payload["decision"] = self._decision(principal)
        payload["view_revision"] = snapshot["rev"]

        decision = payload["decision"]
        previews = decision["ctx"].get("preview_cards", []) if decision is not None else []
        events = self._events(principal, cursor.event_id)
        activity_cards = [card for row in events for card in row.get("cards", ())]
        visible = self._visible_oracles(previews, activity_cards) if cached else self._visible_oracles(snapshot, previews, activity_cards)
        new_oracles = sorted(visible - cursor.seen_oracles)
        if new_oracles:
            payload["defs"] = [self._definition(oracle_id) for oracle_id in new_oracles]
        if events:
            payload["events"] = events

        if not cached:
            cursor.snapshot = copy.deepcopy(snapshot)
        cursor.observation_epoch = observation_epoch
        cursor.observation_principal = principal
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


def _search_presentation(effect: Mapping[str, Any], *, choosing: bool) -> dict[str, str]:
    selector = effect.get("selector") or {}
    basic = "basic" in (selector.get("supertypes") or ()) and "land" in (selector.get("types") or ())
    destination = effect.get("destination")
    if isinstance(destination, str) and destination == "battlefield":
        detail = "The selected card enters the battlefield tapped." if effect.get("enters_tapped_override") else "Put the selection onto the battlefield."
        confirm = "Put onto battlefield"
    elif destination == "hand":
        detail = "Put the selection into your hand."
        confirm = "Put into hand"
    elif destination == "exile":
        detail = "Exile the selection."
        confirm = "Exile selected cards"
    else:
        detail = ""
        confirm = "Confirm selection"
    zone = effect.get("zone", "library")
    if (zone == "library" or "library" in zone) and effect.get("shuffle_after", True):
        detail = (detail + " Shuffle the searched library afterward.").strip()
    return {
        "title": ("Choose a basic land" if choosing else "Search for a basic land?") if basic
            else ("Choose cards from the search" if choosing else "Search for cards?"),
        "detail": detail,
        "accept": confirm if choosing else "Search",
    }
