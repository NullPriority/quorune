from __future__ import annotations

"""Typed fixed-count library exile and temporary play-permission owner."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .combat_damage_library_exile import (
    COMBAT_DAMAGE_CAST_EXILE_VERSION, COMBAT_DAMAGE_COLOR_CAST_EXILE_VERSION,
    COMBAT_DAMAGE_COLOR_CAST_INCARNATION_FIELD, COMBAT_DAMAGE_EXILE_VERSION,
)
from .errors import GameRuleError
from .fractional_quantity_model import HalfQuantitySpec
from .impulse_access_model import (
    SPAWN_SETTLEMENT_IMPULSE_VERSION, SPAWN_IMPULSE_INCARNATION_FIELD,
    POWER_COUNTED_IMPULSE_VERSION, POWER_COUNTED_IMPULSE_INCARNATION_FIELD,
    ImpulseAccessDuration,
    SHUFFLED_TOP_FREE_PLAY_VERSION,
    TOP_FREE_INSTANT_SORCERY_VERSION,
    TemporaryCastPermissionError,
    TemporaryCastPermissionGrant,
    impulse_access_count_is_legal,
    impulse_access_request_count_is_legal,
    impulse_access_play_policy_is_legal,
)
from .model import CardInstance, GameState
from .opponent_library_top_model import OPPONENT_TOP_EXILE_VERSION
from .zone_transitions import ZoneTransitionOwner


class ImpulseAccessHost(Protocol):
    state: GameState

    def _require_seat(self, seat: str, *, in_game: bool = False) -> None: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class ImpulseAccessRequest:
    actor: str
    player: str
    count: int | HalfQuantitySpec
    duration: ImpulseAccessDuration | None
    reason: str
    schema_version: int = 1

    def count_for(self, library_size: int) -> int:
        return self.count.amount(library_size) if isinstance(self.count, HalfQuantitySpec) else self.count

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise GameRuleError(
                "Impulse-access requests require an actor, player, and reason"
            )
        if not impulse_access_request_count_is_legal(self.count, self.schema_version):
            raise GameRuleError("Impulse-access request count is invalid for its version")
        if not impulse_access_play_policy_is_legal(self.actor, self.player, self.duration, self.schema_version):
            raise GameRuleError("Impulse-access requests require their typed supplier and play policy")

    @property
    def beneficiary(self) -> str:
        if self.schema_version in {
            OPPONENT_TOP_EXILE_VERSION, COMBAT_DAMAGE_EXILE_VERSION, COMBAT_DAMAGE_CAST_EXILE_VERSION,
            COMBAT_DAMAGE_COLOR_CAST_EXILE_VERSION,
        }:
            return self.actor
        return self.player


@dataclass(frozen=True, slots=True)
class ImpulseAccessObjectIdentity:
    object_id: str
    logical_object_id: str
    ref: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.object_id, self.logical_object_id, self.ref)
        ):
            raise GameRuleError(
                "Impulse-access object identities must be complete"
            )

    @classmethod
    def from_card(cls, card: CardInstance) -> "ImpulseAccessObjectIdentity":
        return cls(card.object_id, card.logical_object_id, card.ref)


@dataclass(frozen=True, slots=True)
class ImpulseAccessPlan:
    request: ImpulseAccessRequest
    top_first: tuple[ImpulseAccessObjectIdentity, ...]

    def __post_init__(self) -> None:
        identities = tuple(self.top_first)
        if (
            not isinstance(self.request, ImpulseAccessRequest)
            or any(
                not isinstance(value, ImpulseAccessObjectIdentity)
                for value in identities
            )
            or (type(self.request.count) is int and len(identities) > self.request.count)
            or len({value.object_id for value in identities}) != len(identities)
        ):
            raise GameRuleError(
                "Impulse-access plans require a bounded unique top snapshot"
            )
        object.__setattr__(self, "top_first", identities)


@dataclass(frozen=True, slots=True)
class ImpulseAccessResult:
    player: str
    requested_count: int
    exiled_refs: tuple[str, ...]
    moved_refs: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        exiled = tuple(self.exiled_refs)
        moved = tuple(self.moved_refs)
        if (
            type(self.player) is not str
            or not self.player
            or not impulse_access_count_is_legal(self.requested_count, self.schema_version)
            or len(moved) > self.requested_count
            or len(exiled) > len(moved)
            or any(type(value) is not str or not value for value in moved)
            or any(type(value) is not str or not value for value in exiled)
            or not set(exiled).issubset(moved)
        ):
            raise GameRuleError(
                "Impulse-access results require bounded typed card sets"
            )
        object.__setattr__(self, "exiled_refs", exiled)
        object.__setattr__(self, "moved_refs", moved)


def prepare_fixed_impulse_access(
    host: ImpulseAccessHost,
    request: ImpulseAccessRequest,
) -> ImpulseAccessPlan:
    """Snapshot the exact current top cards for one mandatory instruction."""

    host._require_seat(request.actor, in_game=True)
    host._require_seat(request.player, in_game=True)
    library = host.state.players[request.player].zones["library"]
    count = request.count_for(len(library))
    object_ids = tuple(reversed(library[-count:])) if count else ()
    return ImpulseAccessPlan(
        request=request,
        top_first=tuple(
            ImpulseAccessObjectIdentity.from_card(
                host.state.cards[object_id]
            )
            for object_id in object_ids
        ),
    )


def _validate_plan(
    host: ImpulseAccessHost,
    plan: ImpulseAccessPlan,
) -> tuple[str, ...]:
    request = plan.request
    host._require_seat(request.actor, in_game=True)
    host._require_seat(request.player, in_game=True)
    object_ids = tuple(identity.object_id for identity in plan.top_first)
    library = host.state.players[request.player].zones["library"]
    if isinstance(request.count, HalfQuantitySpec) and len(object_ids) != request.count_for(len(library)):
        raise GameRuleError("The rounded half of the library changed before exile committed")
    current_top = (
        tuple(reversed(library[-len(object_ids) :])) if object_ids else ()
    )
    if current_top != object_ids:
        raise GameRuleError(
            "The library top changed before impulse access committed"
        )
    for identity in plan.top_first:
        card = host.state.cards.get(identity.object_id)
        if (
            card is None
            or card.owner != request.player
            or card.zone != "library"
            or card.logical_object_id != identity.logical_object_id
            or card.ref != identity.ref
        ):
            raise GameRuleError(
                "A prepared impulse-access object identity changed"
            )
    return object_ids


def _play_permission(
    host: ImpulseAccessHost,
    request: ImpulseAccessRequest,
) -> dict[str, Any] | None:
    if request.duration is None:
        return None
    spell_types_only = request.schema_version == TOP_FREE_INSTANT_SORCERY_VERSION
    free_play = spell_types_only or request.schema_version in {COMBAT_DAMAGE_EXILE_VERSION, SHUFFLED_TOP_FREE_PLAY_VERSION}
    permission: dict[str, Any] = {
        "player": request.beneficiary,
        "zone": "exile",
        "without_mana_cost": free_play,
        "allow_land": not spell_types_only and request.schema_version not in {
            COMBAT_DAMAGE_CAST_EXILE_VERSION, COMBAT_DAMAGE_COLOR_CAST_EXILE_VERSION,
        },
        "allow_spell": True,
        "duration": request.duration.value,
        "granted_turn_sequence": host.state.turn_sequence,
    }
    if free_play:
        permission["allows_other_spell_costs"] = False
    if request.duration is ImpulseAccessDuration.END_OF_TURN:
        permission["turn_sequence"] = host.state.turn_sequence
    elif request.duration is ImpulseAccessDuration.NEXT_END_STEP:
        permission["granted_during_own_end_step"] = (
            host.state.step == "end_step" and host.state.active_player == request.player
        )
    else:
        permission["expires_at_turns_begun"] = (
            host.state.players[request.player].turns_begun + 1
        )
    return permission


def commit_fixed_impulse_access(
    host: ImpulseAccessHost,
    plan: ImpulseAccessPlan,
) -> ImpulseAccessResult:
    """Move one validated top set and grant only actual exiled objects access."""

    object_ids = _validate_plan(host, plan)
    moved: Sequence[CardInstance] = (
        ZoneTransitionOwner(host).move_cards_simultaneously(
            [(object_id, "exile") for object_id in object_ids],
            reason=plan.request.reason,
            log=False,
        )
        if object_ids
        else ()
    )
    permission = _play_permission(host, plan.request)
    exiled: list[CardInstance] = []
    for card in moved:
        if card.zone != "exile":
            continue
        color_cast = plan.request.schema_version == COMBAT_DAMAGE_COLOR_CAST_EXILE_VERSION
        incarnation_field = {
            SPAWN_SETTLEMENT_IMPULSE_VERSION: SPAWN_IMPULSE_INCARNATION_FIELD,
            POWER_COUNTED_IMPULSE_VERSION: POWER_COUNTED_IMPULSE_INCARNATION_FIELD,
        }.get(plan.request.schema_version)
        needs_visible_incarnation = color_cast or incarnation_field is not None
        if permission is not None and (not needs_visible_incarnation or not card.face_down):
            card.annotations["temporary_play_permission"] = {
                **({incarnation_field: card.logical_object_id} if incarnation_field else {}),
                **permission,
                **({"instant_sorcery_incarnation": card.logical_object_id}
                   if plan.request.schema_version == TOP_FREE_INSTANT_SORCERY_VERSION else {}),
                **({COMBAT_DAMAGE_COLOR_CAST_INCARNATION_FIELD: card.logical_object_id} if color_cast else {}),
            }
        exiled.append(card)
    changed_players = [plan.request.player]
    if exiled and permission is not None and plan.request.beneficiary != plan.request.player:
        changed_players.append(plan.request.beneficiary)
    host._log(
        plan.request.actor,
        "library.impulse_access",
        (
            f"{plan.request.player} exiled {len(exiled)} card(s) with temporary play permission."
            if permission is not None else
            f"{plan.request.player} exiled {len(exiled)} card(s) from their library."
        ),
        {
            "player": plan.request.player,
            "count": len(exiled),
            "objects": [card.ref for card in exiled],
            **({"duration": plan.request.duration.value} if plan.request.duration is not None else {}),
            "reason": plan.request.reason,
        },
        importance=2,
        changed_objects=[card.object_id for card in moved],
        changed_players=changed_players,
    )
    return ImpulseAccessResult(
        player=plan.request.player,
        requested_count=plan.request.count if type(plan.request.count) is int else len(plan.top_first),
        exiled_refs=tuple(card.ref for card in exiled),
        moved_refs=tuple(card.ref for card in moved),
        schema_version=plan.request.schema_version,
    )


def link_power_counted_play_permissions(cards, trigger):
    for card in cards:
        permission = card.annotations.get("temporary_play_permission")
        if isinstance(permission, dict) and permission.get(POWER_COUNTED_IMPULSE_INCARNATION_FIELD) == card.logical_object_id:
            permission["power_counted_impulse_event"] = trigger.ref


def resolve_fixed_impulse_access(
    host: ImpulseAccessHost,
    request: ImpulseAccessRequest,
) -> ImpulseAccessResult:
    return commit_fixed_impulse_access(
        host,
        prepare_fixed_impulse_access(host, request),
    )


def apply_chosen_impulse_access(host, effect, *, actor, reason):
    from .fixed_top_exile import chosen_impulse_grant, chosen_opponent_impulse_grant
    from .opponent_library_exile import grant_chosen_bounded_opponent_play

    opponent = type(effect.get("schema_version")) is int and effect["schema_version"] == 2
    instruction = {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}}
    try:
        if opponent:
            identity, supplier = chosen_opponent_impulse_grant(instruction, actor=actor)
            duration = ImpulseAccessDuration.END_OF_NEXT_TURN
        else:
            identity, duration = chosen_impulse_grant(instruction, actor=actor)
            supplier = actor
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    host._require_seat(actor, in_game=True)
    card = host.state.cards.get(identity.object_id)
    if (card is None or card.zone != "exile" or card.ref != identity.ref or card.owner != supplier
            or card.logical_object_id != identity.logical_object_id or not card.is_card_object or card.face_down):
        raise GameRuleError("Chosen impulse access requires its current exiled card")
    if opponent:
        grant_chosen_bounded_opponent_play(host, card, actor)
    else:
        card.annotations["temporary_play_permission"] = _play_permission(host, ImpulseAccessRequest(actor, actor, 1, duration, reason))
    host._log(actor, "permission.play", f"{actor} may play the chosen exiled card.",
              {"player": actor, "object": card.ref, "duration": duration.value, "reason": reason},
              importance=2, changed_objects=[card.object_id], changed_players=[actor])
    return card.ref


def grant_linked_normal_play(host, *, card, actor):
    """Allow normal play of one actual public exile incarnation until used."""
    if actor not in host.active_seats or card.zone != "exile" or card.face_down or not card.is_card_object:
        raise GameRuleError("Linked normal play requires its public physical exile card")
    card.annotations["temporary_play_permission"] = {
        "player": actor, "zone": "exile", "allow_land": True, "allow_spell": True,
        "without_mana_cost": False, "duration": ImpulseAccessDuration.UNTIL_USED.value,
        "not_before_turn_sequence": host.state.turn_sequence,
        "linked_play_incarnation": card.logical_object_id,
    }


OWNED_NEXT_TURN_EXILE_INCARNATION = "owned_next_turn_exile_incarnation"


def grant_owned_next_turn_exile_play(host, *, card, actor):
    if card.owner != actor or card.zone != "exile" or card.face_down or not card.is_card_object:
        raise GameRuleError("Next-turn play requires a public current owned exile incarnation")
    request = ImpulseAccessRequest(actor, actor, 1, ImpulseAccessDuration.END_OF_NEXT_TURN, "Owned exile play")
    card.annotations["temporary_play_permission"] = {
        **_play_permission(host, request), OWNED_NEXT_TURN_EXILE_INCARNATION: card.logical_object_id,
    }


def grant_temporary_cast_permission(
    host: ImpulseAccessHost,
    *,
    card: CardInstance,
    grant: TemporaryCastPermissionGrant,
    reason: str,
    exclusive_free_cast: bool = False,
) -> str:
    """Commit one typed zone-pinned spell-cast permission."""

    if not isinstance(grant, TemporaryCastPermissionGrant):
        raise GameRuleError("Cast permission requires a typed grant")
    if type(exclusive_free_cast) is not bool or (exclusive_free_cast and not grant.without_mana_cost):
        raise GameRuleError("An exclusive free cast requires a free base price")
    if grant.player not in host.state.players or card.zone == "outside":
        raise GameRuleError("Cast permission requires an in-game player and card")
    permission = {
        "player": grant.player,
        "zone": card.zone,
        "without_mana_cost": grant.without_mana_cost,
        "allow_land": False,
        "allow_spell": True,
        "source": grant.source,
    }
    if exclusive_free_cast:
        permission["allows_other_spell_costs"] = False
    if grant.duration is ImpulseAccessDuration.UNTIL_USED:
        if grant.not_before_turn_sequence <= host.state.turn_sequence:
            raise GameRuleError(
                "Until-used cast permission requires a later turn boundary"
            )
        permission.update(
            {
                "duration": grant.duration.value,
                "not_before_turn_sequence": grant.not_before_turn_sequence,
            }
        )
        duration_text = "on a later turn"
    else:
        permission["turn_sequence"] = host.state.turn_sequence
        duration_text = "this turn"
    card.annotations["temporary_play_permission"] = permission
    details = {
        "player": grant.player,
        "object": card.ref,
        "zone": card.zone,
        "turn_sequence": host.state.turn_sequence,
        "without_mana_cost": grant.without_mana_cost,
        "reason": reason,
    }
    if grant.duration is ImpulseAccessDuration.UNTIL_USED:
        details.update(
            {
                "duration": grant.duration.value,
                "not_before_turn_sequence": grant.not_before_turn_sequence,
            }
        )
    host._log(
        grant.player,
        "permission.cast",
        (
            f"{grant.player} may cast {card.ref} from {card.zone} "
            f"{duration_text}."
        ),
        details,
        visibility=[grant.player, "analyst"],
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[grant.player],
    )
    return card.ref


def apply_single_card_cast_permission(
    host, effect: Mapping[str, Any], *, actor: str, reason: str
) -> str:
    """Validate the selected card and commit its ordinary typed cast grant."""
    card = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={str(effect.get("zone") or "graveyard")},
        owned_only=bool(effect.get("owned_only", True)),
    )
    allowed_types = {
        str(value).casefold()
        for value in effect.get("types_any", [])
    }
    card_types, _, _ = host._type_parts(
        str(
            host._effective_card_data(card).get("type_line")
            or ""
        )
    )
    if allowed_types and not allowed_types.intersection(
        card_types
    ):
        raise GameRuleError(
            "The selected card does not satisfy the cast permission"
        )
    duration = effect.get("duration")
    if duration not in {None, "until_used"}:
        raise GameRuleError("Cast-permission duration is unsupported")
    try:
        grant = TemporaryCastPermissionGrant(
            player=actor,
            duration=(
                ImpulseAccessDuration.UNTIL_USED
                if duration == "until_used"
                else ImpulseAccessDuration.END_OF_TURN
            ),
            not_before_turn_sequence=effect.get(
                "not_before_turn_sequence"
            ),
            without_mana_cost=bool(
                effect.get("without_mana_cost", False)
            ),
            source=str(effect.get("source") or ""),
        )
    except TemporaryCastPermissionError as exc:
        raise GameRuleError(str(exc)) from exc
    return grant_temporary_cast_permission(
        host,
        card=card,
        grant=grant,
        reason=reason,
    )


def grant_void_counter_play_permission(host, effect, *, actor, reason):
    """Grant both ordinary play paths to one reviewed exile incarnation."""
    from .rules.void_counter_play import PERMISSIONS_FIELD, grant_identity, is_eligible_exile_card

    try:
        identity = grant_identity(effect)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    host._require_seat(actor, in_game=True)
    card = host.state.cards.get(identity.object_id)
    if (
        not is_eligible_exile_card(card, actor, host.state.active_seats())
        or card.ref != identity.ref or card.logical_object_id != identity.logical_object_id
    ):
        raise GameRuleError("The chosen void-counter card is no longer an eligible exile incarnation")
    permission = {
        "player": actor, "zone": card.zone, "turn_sequence": host.state.turn_sequence,
        "allow_land": True, "allow_spell": True, "without_mana_cost": True,
        "source": effect["source"], "logical_object_id": identity.logical_object_id,
    }
    card.annotations.setdefault(PERMISSIONS_FIELD, {})[actor] = permission
    host._log(
        actor, "permission.play",
        f"{actor} may play the chosen exiled card this turn without paying its mana cost.",
        {"player": actor, "object": card.ref, "zone": card.zone,
         "turn_sequence": host.state.turn_sequence, "without_mana_cost": True, "reason": reason},
        visibility=[actor, "analyst"] if card.face_down else None,
        importance=2, changed_objects=[card.object_id], changed_players=[actor],
    )
    return card.ref


def temporary_play_permission_for_view(host, seat, card):
    """Check the spell face actually proposed or announced for a restricted grant."""
    permission = current_temporary_play_permission(host.state, seat, card)
    if permission is not None and "instant_sorcery_incarnation" in permission:
        type_line = str(host._effective_card_data(card).get("type_line") or "")
        types, _, _ = host._type_parts(type_line)
        if not types.intersection(("instant", "sorcery")):
            return None
    return permission


def current_temporary_play_permission(state, seat, card):
    """Read concurrent exile grants and the historical single-grant wire format."""
    from .rules.void_counter_play import PERMISSIONS_FIELD

    grants = card.annotations.get(PERMISSIONS_FIELD, {})
    grant = grants.get(seat) if isinstance(grants, Mapping) else None
    if (
        isinstance(grant, Mapping) and is_chosen_exile_play_permission(grant)
        and grant["logical_object_id"] == card.logical_object_id
        and (not card.face_down or seat in card.known_to)
        and temporary_play_permission_is_current(state, seat, card, grant)
    ):
        return grant
    permission = card.annotations.get("temporary_play_permission")
    if isinstance(permission, Mapping) and temporary_play_permission_is_current(state, seat, card, permission):
        return permission
    from .rules.loyalty_graveyard_cast import current_loyalty_graveyard_cast_permission

    return current_loyalty_graveyard_cast_permission(state, seat, card)


def is_chosen_exile_play_permission(permission):
    return isinstance(permission, Mapping) and type(permission.get("logical_object_id")) is str


def expire_current_void_counter_play_permissions(state):
    from .rules.void_counter_play import PERMISSIONS_FIELD

    changed = []
    for card in state.cards.values():
        grants = card.annotations.get(PERMISSIONS_FIELD)
        if not isinstance(grants, Mapping):
            continue
        retained = {
            seat: grant for seat, grant in grants.items()
            if isinstance(grant, Mapping) and type(grant.get("turn_sequence")) is int
            and grant["turn_sequence"] > state.turn_sequence
        }
        if retained:
            card.annotations[PERMISSIONS_FIELD] = retained
        else:
            card.annotations.pop(PERMISSIONS_FIELD)
        if retained != grants:
            changed.append(card.object_id)
    return changed


def temporary_play_permission_is_current(
    state: GameState,
    seat: str,
    card: CardInstance,
    permission: Mapping[str, Any],
) -> bool:
    """Validate one zone-pinned temporary permission at its duration boundary."""

    if (
        str(permission.get("player") or "") != seat
        or str(permission.get("zone") or "") != card.zone
    ):
        return False
    if SPAWN_IMPULSE_INCARNATION_FIELD in permission and (
        permission[SPAWN_IMPULSE_INCARNATION_FIELD] != card.logical_object_id
        or card.zone != "exile" or card.face_down or not card.is_card_object
        or permission.get("allow_land") is not True or permission.get("allow_spell") is not True
        or permission.get("without_mana_cost") is not False
        or permission.get("duration") != ImpulseAccessDuration.END_OF_TURN.value
    ):
        return False
    if POWER_COUNTED_IMPULSE_INCARNATION_FIELD in permission:
        end_step_ref = permission.get("power_counted_impulse_event")
        return (
            permission[POWER_COUNTED_IMPULSE_INCARNATION_FIELD] == card.logical_object_id
            and card.zone == "exile" and not card.face_down and card.is_card_object
            and permission.get("allow_land") is True and permission.get("allow_spell") is True
            and permission.get("without_mana_cost") is False
            and permission.get("duration") == ImpulseAccessDuration.NEXT_END_STEP.value
            and type(end_step_ref) is str and any(
                trigger.active and trigger.ref == end_step_ref and trigger.controller == seat
                and card.object_id in trigger.referred_object_ids for trigger in state.delayed_triggers
            )
        )
    if OWNED_NEXT_TURN_EXILE_INCARNATION in permission and (
        permission[OWNED_NEXT_TURN_EXILE_INCARNATION] != card.logical_object_id
        or card.owner != seat or card.zone != "exile" or card.face_down or not card.is_card_object
        or permission.get("allow_land") is not True or permission.get("allow_spell") is not True
        or permission.get("without_mana_cost") is not False
        or permission.get("duration") != ImpulseAccessDuration.END_OF_NEXT_TURN.value
    ):
        return False
    if "instant_sorcery_incarnation" in permission and (
        permission["instant_sorcery_incarnation"] != card.logical_object_id
        or card.zone != "exile" or card.face_down or permission.get("allow_land") is not False
        or permission.get("allow_spell") is not True or permission.get("without_mana_cost") is not True
        or permission.get("allows_other_spell_costs") is not False
        or permission.get("duration") != ImpulseAccessDuration.END_OF_TURN.value
    ):
        return False
    duration = permission.get("duration")
    if COMBAT_DAMAGE_COLOR_CAST_INCARNATION_FIELD in permission and (
        permission[COMBAT_DAMAGE_COLOR_CAST_INCARNATION_FIELD] != card.logical_object_id
        or card.zone != "exile" or card.face_down or not card.is_card_object
        or permission.get("allow_land") is not False or permission.get("allow_spell") is not True
        or permission.get("without_mana_cost") is not False
        or duration != ImpulseAccessDuration.END_OF_TURN.value
    ):
        return False
    from .private_opponent_library_cast_model import (
        PRIVATE_OPPONENT_LIBRARY_CAST_DURATION, private_opponent_library_cast_is_current,
    )

    if duration == PRIVATE_OPPONENT_LIBRARY_CAST_DURATION:
        return private_opponent_library_cast_is_current(state, seat, card, permission)
    if duration in {None, ImpulseAccessDuration.END_OF_TURN.value}:
        turn_sequence = permission.get("turn_sequence")
        return (
            type(turn_sequence) is int
            and turn_sequence == state.turn_sequence
        )
    if duration == ImpulseAccessDuration.UNTIL_USED.value:
        if "linked_play_incarnation" in permission and (
            permission["linked_play_incarnation"] != card.logical_object_id or card.zone != "exile" or card.face_down
        ):
            return False
        not_before = permission.get("not_before_turn_sequence")
        return (
            type(not_before) is int
            and not_before >= 0
            and state.turn_sequence >= not_before
        )
    if duration == ImpulseAccessDuration.NEXT_END_STEP.value:
        granted = permission.get("granted_turn_sequence")
        inside_step = permission.get("granted_during_own_end_step")
        return (type(granted) is int and 0 <= granted <= state.turn_sequence and type(inside_step) is bool
                and (state.step != "end_step" or state.active_player != seat
                     or (granted == state.turn_sequence and inside_step)))
    if duration != ImpulseAccessDuration.END_OF_NEXT_TURN.value:
        return False
    expires = permission.get("expires_at_turns_begun")
    return (
        type(expires) is int
        and expires >= 0
        and state.players[seat].turns_begun <= expires
    )


def expire_next_end_step_play_permissions(state: GameState, *, active_player: str) -> None:
    for card in state.cards.values():
        permission = card.annotations.get("temporary_play_permission")
        if (isinstance(permission, Mapping) and permission.get("player") == active_player
                and permission.get("duration") == ImpulseAccessDuration.NEXT_END_STEP.value):
            card.annotations.pop("temporary_play_permission")


def expire_temporary_play_permissions(
    state: GameState,
    *,
    active_player: str | None,
) -> tuple[str, ...]:
    """Expire current-turn and active player's next-turn grants at cleanup."""

    changed = expire_current_void_counter_play_permissions(state)
    for card in state.cards.values():
        permission = card.annotations.get("temporary_play_permission")
        if not isinstance(permission, Mapping):
            continue
        player = str(permission.get("player") or "")
        duration = permission.get("duration")
        remove = False
        if duration in {None, ImpulseAccessDuration.END_OF_TURN.value}:
            turn_sequence = permission.get("turn_sequence")
            remove = (
                type(turn_sequence) is int
                and turn_sequence <= state.turn_sequence
            )
        elif (
            duration == ImpulseAccessDuration.END_OF_NEXT_TURN.value
            and player == active_player
            and player in state.players
        ):
            expires = permission.get("expires_at_turns_begun")
            remove = (
                type(expires) is int
                and state.players[player].turns_begun >= expires
            )
        if remove:
            card.annotations.pop("temporary_play_permission", None)
            changed.append(card.object_id)
    return tuple(dict.fromkeys(changed))


__all__ = [
    "commit_fixed_impulse_access",
    "expire_temporary_play_permissions",
    "ImpulseAccessDuration",
    "ImpulseAccessHost",
    "ImpulseAccessObjectIdentity",
    "ImpulseAccessPlan",
    "ImpulseAccessRequest",
    "ImpulseAccessResult",
    "prepare_fixed_impulse_access",
    "grant_temporary_cast_permission",
    "resolve_fixed_impulse_access",
    "temporary_play_permission_is_current",
]
