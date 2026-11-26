"""Private play authority for committed opponent-library top arrivals."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError


FIELD = "private_top_exile_play"
DURATION = "private_top_exile_incarnation"


def has_private_top_exile_designation(card):
    return FIELD in card.annotations


def current_private_top_exile_permission(state, seat, card):
    raw = card.annotations.get(FIELD)
    player = state.players.get(seat)
    if (card.zone != "exile" or not card.is_card_object
            or not isinstance(raw, Mapping) or set(raw) != {"schema_version", "viewer", "identity"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] not in {1, 2}
            or raw["schema_version"] == 1 and card.owner == seat
            or raw["viewer"] != seat or raw["identity"] != card.logical_object_id
            or player is None or not player.in_game
            or card.face_down and tuple(card.known_to) != (seat,)):
        return None
    permission = {"player": seat, "zone": "exile", "without_mana_cost": False,
                  "allow_land": True, "allow_spell": True, "duration": DURATION}
    if raw["schema_version"] == 2:
        permission["allow_any_mana_type"] = True
    return permission


def private_top_exile_viewer(state, card):
    raw = card.annotations.get(FIELD)
    viewer = raw.get("viewer") if isinstance(raw, Mapping) else None
    if type(viewer) is str and current_private_top_exile_permission(state, viewer, card):
        return viewer
    return None


def library_top_changes(host, actor):
    changes = []
    for owner in host.apnap_order():
        if owner == actor:
            continue
        library = host.state.players[owner].zones["library"]
        if not library:
            continue
        card = host.state.cards.get(library[-1])
        if card is None or card.zone != "library" or card.owner != owner or not card.is_card_object:
            raise GameRuleError("An opposing library top lost its owned card identity")
        changes.append((card.object_id, "exile"))
    return changes


def _damaged_player_top_changes(host, owner):
    player = host.state.players.get(owner)
    if player is None:
        raise GameRuleError("The damage event did not identify a player")
    library = player.zones["library"]
    if not player.in_game or not library:
        return ()
    card = host.state.cards.get(library[-1])
    if card is None or card.zone != "library" or card.owner != owner or not card.is_card_object:
        raise GameRuleError("The damaged player's library lost its original top card")
    return ((card.object_id, "exile"),)


@dataclass(frozen=True, slots=True)
class PrivateTopExileBatch:
    viewer: str
    source: ResolutionEffectSource
    damage_recipient: str | None = None

    def validate(self, host, changes):
        from ..opponent_library_exile_model import (
            DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY, DAMAGED_PLAYER_PRIVATE_TOP_EVENT, PRIVATE_TOP_EXILE_CAPABILITY,
        )
        from .opponent_library_exile_triggers import library_exile_trigger_runtime_dependencies

        item = next((item for item in host.state.stack if item.ref == self.source.stack_ref), None)
        if (item is None or item.kind != "triggered_ability" or item.controller != self.viewer
                or self.viewer not in host.active_seats or item.context.get("currently_resolving") is not True
                or item.source_object_id != self.source.object_id
                or item.context.get("source_logical_object_id") != self.source.logical_object_id
                or item.targets):
            raise GameRuleError("Private library exile requires the complete original resolving batch")
        if self.damage_recipient is None:
            expected = library_top_changes(host, self.viewer)
            capability = PRIVATE_TOP_EXILE_CAPABILITY
        else:
            context = item.context
            if ((context.get("event"), context.get("target_kind"), context.get("target"))
                    != (DAMAGED_PLAYER_PRIVATE_TOP_EVENT, "player", self.damage_recipient)
                    or context.get("combat") is not True):
                raise GameRuleError("Private damage exile lost the original damaged player")
            expected = _damaged_player_top_changes(host, self.damage_recipient)
            capability = DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY
        if tuple(changes) != tuple(expected):
            raise GameRuleError("Private library exile requires the actual authorized library top")
        program = host.semantics.get(item.semantic_key)
        try:
            if (program is None or not host.semantic_program_is_current_trusted(program)
                    or capability not in library_exile_trigger_runtime_dependencies(program)):
                raise ValueError("Private library exile requires its admitted original trigger")
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError(str(exc)) from exc

    def grant_committed(self, card):
        if card.zone == "exile" and card.face_down:
            card.known_to = [self.viewer]
            card.annotations[FIELD] = {
                "schema_version": 2 if self.damage_recipient is not None else 1,
                "viewer": self.viewer, "identity": card.logical_object_id,
            }


def apply_private_damage_top_exile(host, effect, actor, reason):
    from ..semantic_runtime.intents import RevealLibraryCardsIntent
    from ..zone_transitions import ZoneTransitionOwner

    recipient = effect["library_owner"]
    try:
        source = ResolutionEffectSource.from_effect(effect)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    batch = PrivateTopExileBatch(actor, source, recipient)
    changes = _damaged_player_top_changes(host, recipient)
    batch.validate(host, changes)
    if not changes:
        return 0
    (object_id, _destination), = changes
    ref = host.state.cards[object_id].ref
    host.reveal_library_cards_intent(RevealLibraryCardsIntent(actor, recipient, actor, (ref,), reason))
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(changes, reason=reason, private_top_exile=batch)
    count = sum(card.zone == "exile" for card in moved)
    if count:
        host._log(
            actor, "library.damaged_player_exile_top", "A damaged player exiled the top card of their library face down.",
            {"count": count, "player": recipient}, changed_players=list(dict.fromkeys((recipient, actor))),
        )
    return count
