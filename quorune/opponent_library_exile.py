from __future__ import annotations

"""Apply actual opponent-library movement and incarnation-scoped play access."""

from collections.abc import Mapping

from .errors import GameRuleError
from .impulse_access import temporary_play_permission_is_current
from .impulse_access_model import ImpulseAccessDuration
from .opponent_library_exile_model import (
    ATTACKING_OPPONENT_TOP_EXILE_VERSION, DAMAGED_PLAYER_PRIVATE_TOP_VERSION, PRIVATE_TOP_EXILE_VERSION,
    validate_each_opponent_top_exile,
    validate_opponent_library_exile_play,
)
from .zone_transitions import ZoneTransitionOwner


PLAY_PERMISSIONS_FIELD = "opponent_library_exile_play_permissions"
INCARNATION_PLAY_FIELD = "opponent_library_exile_incarnation_play"
LEGACY_FREE_EXILE_CAST_FIELD = "legacy_free_exile_play"


def _instruction(effect):
    return {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}}


def apply_each_opponent_top_exile(host, effect, *, actor, operation, reason):
    del operation
    try:
        grant_play = validate_each_opponent_top_exile(_instruction(effect), actor=actor)
    except (TypeError, ValueError) as error:
        raise GameRuleError(str(error)) from error
    host._require_seat(actor, in_game=True)
    if effect["schema_version"] == DAMAGED_PLAYER_PRIVATE_TOP_VERSION:
        from .rules.private_opponent_top_exile import apply_private_damage_top_exile

        return apply_private_damage_top_exile(host, effect, actor, reason)
    from .rules.private_opponent_top_exile import PrivateTopExileBatch, library_top_changes

    if effect["schema_version"] == ATTACKING_OPPONENT_TOP_EXILE_VERSION:
        from .rules.attacking_opponent_library_exile import validate_attack_top_exile_source

        attacker = validate_attack_top_exile_source(host, effect, actor)
        opponents = [attacker] if attacker in host.active_seats else []
        changes = []
        for seat in opponents:
            library = host.state.players[seat].zones["library"]
            if not library:
                continue
            card = host.state.cards.get(library[-1])
            if card is None or card.zone != "library" or card.owner != seat or not card.is_card_object:
                raise GameRuleError("An opposing library top lost its owned card identity")
            changes.append((card.object_id, "exile"))
    else:
        opponents = [seat for seat in host.apnap_order() if seat != actor]
        changes = library_top_changes(host, actor)
    private = None
    if effect["schema_version"] == PRIVATE_TOP_EXILE_VERSION:
        from .continuous_effect_state import ResolutionEffectSource

        try:
            private = PrivateTopExileBatch(actor, ResolutionEffectSource.from_effect(effect))
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        changes, reason=reason, **({"private_top_exile":private} if private is not None else {}),
    )
    exiled = tuple(card for card in moved if card.zone == "exile")
    public = tuple(card for card in exiled if not card.face_down)
    if grant_play:
        for card in public:
            card.annotations[INCARNATION_PLAY_FIELD] = (actor, card.logical_object_id)
    host._log(
        actor, "library.opponents_exile_top", f"Opponents exiled {len(exiled)} card(s) from their libraries.",
        {"count": len(exiled), "objects": [card.ref for card in public], "reason": reason},
        importance=2, changed_objects=[card.object_id for card in public],
        changed_players=[*opponents, actor] if grant_play and public or private is not None and exiled else opponents,
    )
    return len(exiled)


def grant_controlled_search_exile_play(card, player):
    if card.zone != "exile" or card.face_down or not card.is_card_object or card.owner == player:
        raise GameRuleError("Search permission requires the found public exile incarnation")
    card.annotations[INCARNATION_PLAY_FIELD] = (player, card.logical_object_id)


def _existing_permissions(card):
    raw = card.annotations.get(PLAY_PERMISSIONS_FIELD)
    return raw if isinstance(raw, Mapping) else {}


def apply_opponent_library_exile_play(host, effect, *, actor, operation, reason):
    del operation
    try:
        occurrence = validate_opponent_library_exile_play(_instruction(effect), actor=actor)
    except (TypeError, ValueError) as error:
        raise GameRuleError(str(error)) from error
    host._require_seat(actor, in_game=True)
    host._require_seat(occurrence.library_owner)
    exiled = []
    for identity in occurrence.objects:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.logical_object_id != identity.logical_object_id
                or card.zone != "exile" or not card.is_card_object):
            continue
        if card.owner != occurrence.library_owner:
            raise GameRuleError("Opponent-library permission lost its original library owner")
        exiled.append(card)
    player = host.state.players[actor]
    for card in exiled:
        card.annotations[PLAY_PERMISSIONS_FIELD] = {
            **_existing_permissions(card),
            actor: {
                "schema_version": 1,
                "logical_object_id": card.logical_object_id,
                "expires_at_turns_begun": player.turns_begun + 1,
            },
        }
    public = tuple(card for card in exiled if not card.face_down)
    if public:
        host._log(
            actor, "permission.opponent_library_exile_play", "The controller may play these exiled cards until the end of their next turn.",
            {"player": actor, "objects": [card.ref for card in public], "reason": reason},
            importance=2, changed_objects=[card.object_id for card in public], changed_players=[actor],
        )
    return len(public)


def grant_chosen_bounded_opponent_play(host, card, actor):
    card.annotations[PLAY_PERMISSIONS_FIELD] = {
        **_existing_permissions(card),
        actor: {"schema_version": 2, "logical_object_id": card.logical_object_id,
                "expires_at_turns_begun": host.state.players[actor].turns_begun + 1},
    }


def _current_bounded_exile_play_permission(state, seat, card):
    """Return the next-turn permission for this original face-up object."""
    if card.zone != "exile" or card.face_down or not card.is_card_object or seat not in state.players:
        return None
    stored = _existing_permissions(card).get(seat)
    if not isinstance(stored, Mapping) or set(stored) != {
        "schema_version", "logical_object_id", "expires_at_turns_begun",
    }:
        return None
    if (type(stored["schema_version"]) is not int or stored["schema_version"] not in {1, 2}
            or stored["logical_object_id"] != card.logical_object_id):
        return None
    permission = {
        "player": seat, "zone": "exile", "without_mana_cost": False,
        "allow_land": True, "allow_spell": True, "duration": ImpulseAccessDuration.END_OF_NEXT_TURN.value,
        "expires_at_turns_begun": stored["expires_at_turns_begun"],
        **({"color_only": True} if stored["schema_version"] == 2 else {}),
    }
    return permission if temporary_play_permission_is_current(state, seat, card, permission) else None


def current_opponent_library_exile_play_permission(state, seat, card):
    """Return an ordinary-play grant limited to the original public exile object."""
    bounded = _current_bounded_exile_play_permission(state, seat, card)
    if bounded is not None:
        return bounded
    from .rules.conditional_exile_cast import current_conditional_exile_cast
    from .rules.private_opponent_top_exile import current_private_top_exile_permission

    conditional = current_conditional_exile_cast(state, seat, card)
    if conditional is not None:
        return conditional

    private = current_private_top_exile_permission(state, seat, card)
    if private is not None:
        return private
    from .rules.opponents_graveyards_exile_cast import current_graveyard_exile_cast_permission

    identity = card.annotations.get(INCARNATION_PLAY_FIELD)
    player = state.players.get(seat)
    if (
        card.zone != "exile"
        or card.face_down or not card.is_card_object
        or not isinstance(identity, (tuple, list))
        or tuple(identity) != (seat, card.logical_object_id)
        or player is None or not player.in_game
    ):
        return current_graveyard_exile_cast_permission(state, seat, card)
    return {
        "player": seat, "zone": "exile", "without_mana_cost": False,
        "allow_land": True, "allow_spell": True, "duration": "while_exiled",
    }


def opponent_library_exile_spend_context(state, seat, card, spend_context):
    from .rules.opponents_graveyards_exile_cast import current_graveyard_exile_cast_permission
    from .combat_damage_library_exile import COMBAT_DAMAGE_COLOR_CAST_INCARNATION_FIELD
    from .impulse_access import current_temporary_play_permission
    from .rules.private_opponent_top_exile import DURATION

    permission = (current_graveyard_exile_cast_permission(state, seat, card)
                  or current_opponent_library_exile_play_permission(state, seat, card))

    if permission is None and card.zone == "exile":
        temporary = current_temporary_play_permission(state, seat, card)
        if temporary is not None and COMBAT_DAMAGE_COLOR_CAST_INCARNATION_FIELD in temporary:
            from .mana_restrictions import exile_play_mana_spend_context

            return exile_play_mana_spend_context(spend_context, seat, color_only=True)

    permission = current_graveyard_exile_cast_permission(state, seat, card) or current_opponent_library_exile_play_permission(state, seat, card)

    if permission is None or (permission["duration"] == DURATION and not permission.get("allow_any_mana_type")):
        return spend_context
    from .mana_restrictions import exiled_spell_any_type_payer, exile_play_mana_spend_context

    if exiled_spell_any_type_payer(spend_context) == seat:
        return spend_context
    return exile_play_mana_spend_context(
        spend_context, seat, color_only=permission["duration"] == "while_exiled" or permission.get("color_only") is True,
    )


def opponent_library_exile_cast_mode(
    state, seat, card, permission, *, force_without_mana_cost, alternative_base,
    intrinsic_disturb, resolving_exile_authority, library_life_base, ordinary_private_authority=False,
):
    """Resolve this bounded permission's free versus separately-payable cast mode."""

    legacy_free = bool(
        permission and permission.get("without_mana_cost") and not intrinsic_disturb
        and not resolving_exile_authority and not library_life_base
    )
    separately_payable = (
        legacy_free and not force_without_mana_cost and alternative_base is None
        and (ordinary_private_authority or current_opponent_library_exile_play_permission(state, seat, card) is not None)
    )
    return force_without_mana_cost or (legacy_free and not separately_payable), separately_payable


def commit_opponent_library_exile_spend_context(host, proposal, card, selected_option, details, type_line_field):
    from .rules.casting.spell_mana_payments import cast_mana_spend_context

    spend_context = cast_mana_spend_context(host, proposal.seat, card, details.get(type_line_field, proposal.type_line))
    if not (details.get("during_resolution") or selected_option.get(LEGACY_FREE_EXILE_CAST_FIELD)):
        spend_context = opponent_library_exile_spend_context(host.state, proposal.seat, card, spend_context)
    return spend_context


def separately_payable_exile_cast_base_options(
    host, seat, card, program, *, x_value, hint, has_x, suppress_source_costs,
    printed_base_options, zone_base_options,
):
    """Build the optional ordinary-cost branch beside a free exile-play mode."""
    free = printed_base_options(
        host, seat, card, program, x_value=x_value, hint=hint, cast_without_mana=True,
        alternative_base=None, suppress_source_costs=suppress_source_costs,
        resolving_paid_zone=None,
    )
    if free is None:
        return ()
    for option in free[0]:
        option[LEGACY_FREE_EXILE_CAST_FIELD] = 1
        if has_x:
            option["x_value_policy"] = "zero"
    return zone_base_options(
        host, seat, card, free[0], cast_without_mana=True, force_without_mana_cost=False,
        alternative_base=None, suppress_source_costs=suppress_source_costs,
        resolving_paid_zone=None,
    )


def expire_opponent_library_exile_play_permissions(state, *, active_player):
    """Release this player's bounded access at the owning cleanup boundary."""
    player = state.players.get(active_player)
    if player is None:
        return ()
    changed = []
    exile = (object_id for owner in state.players.values() for object_id in owner.zones["exile"])
    for object_id in exile:
        card = state.cards[object_id]
        permissions = _existing_permissions(card)
        grant = permissions.get(active_player)
        if not isinstance(grant, Mapping):
            continue
        expires = grant.get("expires_at_turns_begun")
        if type(expires) is not int or player.turns_begun < expires:
            continue
        retained = {seat: value for seat, value in permissions.items() if seat != active_player}
        if retained:
            card.annotations[PLAY_PERMISSIONS_FIELD] = retained
        else:
            card.annotations.pop(PLAY_PERMISSIONS_FIELD)
        if card.zone == "exile" and not card.face_down:
            changed.append(card.object_id)
    return tuple(changed)
