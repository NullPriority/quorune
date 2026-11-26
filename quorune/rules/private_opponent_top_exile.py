"""Private play authority for committed opponent-library top arrivals."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_state import ResolutionEffectSource
from .controller_private_top_exile_model import CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY, ControllerPrivateTopExile
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


def _controller_top_exile_designation(state, card):
    raw = card.annotations.get(FIELD)
    if (card.zone != "exile" or not card.is_card_object or not isinstance(raw, Mapping)
            or set(raw) != {"schema_version", "viewer", "identity", "controlled_subtype"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 3
            or raw["identity"] != card.logical_object_id
            or type(raw["viewer"]) is not str or raw["viewer"] != card.owner
            or raw["viewer"] not in state.players or not state.players[raw["viewer"]].in_game
            or card.face_down and tuple(card.known_to) != (raw["viewer"],)):
        return None
    try:
        return raw["viewer"], ControllerPrivateTopExile(raw["controlled_subtype"])
    except ValueError:
        return None


def controller_private_top_exile_play_permission(host, seat, card):
    from ..characteristic_evaluation import type_parts

    designation = _controller_top_exile_designation(host.state, card)
    if designation is None or designation[0] != seat:
        return None
    subtype = designation[1].subtype
    if not any(candidate.controller == seat and candidate.zone == "battlefield" and not candidate.phased_out
               and subtype in type_parts(str(host._effective_card_data(candidate).get("type_line") or ""))[1]
               for candidate in host._semantic_event_sources(zones={"battlefield"})):
        return None
    return {"player": seat, "zone": "exile", "without_mana_cost": False,
            "allow_land": True, "allow_spell": True, "duration": DURATION}


def private_top_exile_viewer(state, card):
    designation = _controller_top_exile_designation(state, card)
    if designation is not None:
        return designation[0]
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


def _controller_top_changes(host, owner):
    library = host.state.players[owner].zones["library"]
    changes = []
    for object_id in reversed(library[-2:]):
        card = host.state.cards.get(object_id)
        if card is None or card.zone != "library" or card.owner != owner or not card.is_card_object:
            raise GameRuleError("The controller's library lost an original top card")
        changes.append((object_id, "exile"))
    return tuple(changes)


def _validate_controller_top_batch(host, source, viewer, spec):
    from ..semantic_runtime.context import semantic_source_context

    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != viewer
            or viewer not in host.active_seats or item.context.get("currently_resolving") is not True
            or item.targets or semantic_source_context(item, host.state.cards).to_dict() != source.to_dict()):
        raise GameRuleError("Private controller-library exile requires its original resolving spell")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or CONTROLLER_PRIVATE_TOP_EXILE_CAPABILITY not in program.capability_dependencies
            or tuple(program.effects) != (spec.instruction(),)):
        raise GameRuleError("Private controller-library exile requires its admitted instruction")


@dataclass(frozen=True, slots=True)
class PrivateTopExileBatch:
    viewer: str
    source: ResolutionEffectSource
    damage_recipient: str | None = None
    controller_top: ControllerPrivateTopExile | None = None

    def validate(self, host, changes):
        if self.controller_top is not None:
            if not isinstance(self.controller_top, ControllerPrivateTopExile) or self.damage_recipient is not None:
                raise GameRuleError("Private controller-library exile has more than one source")
            _validate_controller_top_batch(host, self.source, self.viewer, self.controller_top)
            if tuple(changes) != _controller_top_changes(host, self.viewer):
                raise GameRuleError("Private controller-library exile requires the actual current top cards")
            return
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
            if self.controller_top is not None:
                card.annotations[FIELD].update(schema_version=3, controlled_subtype=self.controller_top.subtype)


def apply_controller_private_top_exile(host, effect, *, actor, operation, reason):
    from ..semantic_runtime.intents import RevealLibraryCardsIntent
    from ..zone_transitions import ZoneTransitionOwner

    try:
        source = ResolutionEffectSource.from_effect(effect)
        instruction = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
        spec = ControllerPrivateTopExile.from_instruction(instruction, actor=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if actor not in host.active_seats:
        raise GameRuleError("Private controller-library exile requires its active controller")
    batch = PrivateTopExileBatch(actor, source, controller_top=spec)
    changes = _controller_top_changes(host, actor)
    batch.validate(host, changes)
    if not changes:
        return 0
    refs = tuple(host.state.cards[object_id].ref for object_id, _ in changes)
    host.reveal_library_cards_intent(RevealLibraryCardsIntent(actor, actor, actor, refs, reason))
    return sum(card.zone == "exile" for card in
               ZoneTransitionOwner(host).move_cards_simultaneously(changes, reason=reason, private_top_exile=batch))


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
