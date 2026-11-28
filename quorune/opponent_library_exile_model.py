from __future__ import annotations

"""Source-bound instructions and committed opponent-library exile facts."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity


EACH_OPPONENT_TOP_EXILE_OPERATION = "exile_each_opponent_library_top"
EACH_OPPONENT_TOP_EXILE_CAPABILITY = "library.exile.each_opponent_top"
PRIVATE_TOP_EXILE_CAPABILITY = "library.exile.each_opponent_top_private_play"
PRIVATE_TOP_EXILE_VERSION = 3
ATTACKING_OPPONENT_TOP_EXILE_CAPABILITY = "library.exile.attacking_opponent_top_play"
ATTACKING_OPPONENT_TOP_EXILE_VERSION = 4
DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY = "library.exile.equipped_damage_player_top_private_play"
DAMAGED_PLAYER_PRIVATE_TOP_VERSION = 5
DAMAGED_PLAYER_PRIVATE_TOP_EVENT = "damage.dealt"
DAMAGED_PLAYER_PRIVATE_TOP_VARIANT = "equipped_creature_combat_damage_a_player"
EXILE_INCARNATION_PLAY_CAPABILITY = "permission.exile.opponent_library_incarnation"
OPPONENT_LIBRARY_EXILE_PLAY_OPERATION = "grant_exiled_opponent_library_play"
OPPONENT_LIBRARY_EXILE_PLAY_CAPABILITY = "permission.exile.controlled_opponent_library_batch"
OPPONENT_LIBRARY_EXILE_EVENT = "library.opponent_exile_batch"
OPPONENT_LIBRARY_EXILE_FIELD = "opponent_library_exile"
OPPONENT_LIBRARY_EXILE_ACTOR_FIELD = "effect_controller"


@dataclass(frozen=True, slots=True)
class OpponentLibraryExileOccurrence:
    """Actual zone-change results for one opponent's library in one batch."""

    effect_controller: str
    library_owner: str
    objects: tuple[ContinuousObjectIdentity, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Opponent-library exile occurrence version is unsupported")
        if any(type(seat) is not str or not seat for seat in (self.effect_controller, self.library_owner)):
            raise ValueError("Opponent-library exile requires the cause controller and library owner")
        if self.effect_controller == self.library_owner:
            raise ValueError("Opponent-library exile requires a different library owner")
        if not isinstance(self.objects, tuple) or not self.objects:
            raise ValueError("Opponent-library exile requires a nonempty committed object set")
        for identity in self.objects:
            if not isinstance(identity, ContinuousObjectIdentity):
                raise ValueError("Opponent-library exile requires typed object identities")
            physical, separator, incarnation = identity.logical_object_id.rpartition("@")
            if (not separator or physical != identity.object_id or not incarnation.isascii()
                    or not incarnation.isdecimal() or incarnation.startswith("0")):
                raise ValueError("Opponent-library exile requires an exact post-move incarnation")
        if len({identity.object_id for identity in self.objects}) != len(self.objects):
            raise ValueError("Opponent-library exile cannot repeat an object")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "effect_controller": self.effect_controller,
            "library_owner": self.library_owner,
            "objects": [identity.to_dict() for identity in self.objects],
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> OpponentLibraryExileOccurrence:
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "effect_controller", "library_owner", "objects",
        } or not isinstance(value["objects"], (list, tuple)):
            raise ValueError("Opponent-library exile occurrence has a closed schema")
        return cls(
            effect_controller=value["effect_controller"], library_owner=value["library_owner"],
            objects=tuple(ContinuousObjectIdentity.from_dict(item) for item in value["objects"]),
            schema_version=value["schema_version"],
        )

    def event_context(self) -> dict:
        return {
            OPPONENT_LIBRARY_EXILE_ACTOR_FIELD: self.effect_controller,
            OPPONENT_LIBRARY_EXILE_FIELD: self.to_dict(),
        }


def each_opponent_top_exile_effect(*, grant_incarnation_play: bool = False) -> dict:
    """Build either mandatory top exile or its ordinary-play/color-only result."""
    return {
        "op": EACH_OPPONENT_TOP_EXILE_OPERATION,
        "schema_version": 2 if grant_incarnation_play else 1,
        "controller": "$controller",
    }


def private_top_exile_effect():
    return {**each_opponent_top_exile_effect(), "schema_version": PRIVATE_TOP_EXILE_VERSION}


def attacking_opponent_top_exile_effect() -> dict:
    return {**each_opponent_top_exile_effect(), "schema_version": ATTACKING_OPPONENT_TOP_EXILE_VERSION,
            "library_owner": "$context.player"}


def damaged_player_private_top_effect() -> dict:
    return {**each_opponent_top_exile_effect(), "schema_version": DAMAGED_PLAYER_PRIVATE_TOP_VERSION,
            "library_owner": "$context.target"}


def damaged_player_private_top_condition() -> dict:
    return {"all": [
        {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}


def opponent_library_exile_play_effect() -> dict:
    return {
        "op": OPPONENT_LIBRARY_EXILE_PLAY_OPERATION, "schema_version": 1, "player": "$controller",
        "occurrence": f"$context.{OPPONENT_LIBRARY_EXILE_FIELD}",
    }


def validate_each_opponent_top_exile(effect: Mapping, *, actor: str | None = None) -> bool:
    if not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int:
        raise ValueError("Top exile requires its original controller and each opposing library")
    if effect["schema_version"] == ATTACKING_OPPONENT_TOP_EXILE_VERSION:
        shape = attacking_opponent_top_exile_effect()
        if actor is not None:
            owner = effect.get("library_owner")
            if type(owner) is not str or not owner or owner == actor or owner.startswith("$"):
                raise ValueError("Attacking-opponent top exile requires its actual opponent.")
            shape.update(controller=actor, library_owner=owner)
        if effect != shape:
            raise ValueError("Attacking-opponent top exile requires its original controller and opponent.")
        return True
    if effect["schema_version"] == DAMAGED_PLAYER_PRIVATE_TOP_VERSION:
        shape = damaged_player_private_top_effect()
        if actor is not None:
            owner = effect.get("library_owner")
            if type(owner) is not str or not owner or owner.startswith("$"):
                raise ValueError("The private damage result requires its actual damaged player")
            shape.update(controller=actor, library_owner=owner)
        if effect != shape:
            raise ValueError("The private damage result requires its exact controller and library owner")
        return True
    grant_incarnation_play = effect["schema_version"] == 2
    shape = (private_top_exile_effect() if effect["schema_version"] == PRIVATE_TOP_EXILE_VERSION
             else each_opponent_top_exile_effect(grant_incarnation_play=grant_incarnation_play))
    if actor is not None:
        shape["controller"] = actor
    if effect != shape:
        raise ValueError("Top exile requires its original controller and each opposing library")
    return grant_incarnation_play


def validate_opponent_library_exile_play(
    effect: Mapping, *, actor: str | None = None,
) -> OpponentLibraryExileOccurrence | None:
    shape = opponent_library_exile_play_effect()
    if actor is not None:
        shape.update(player=actor, occurrence=effect.get("occurrence"))
    if effect != shape or type(effect.get("schema_version")) is not int:
        raise ValueError("Exile play permission requires its exact triggered occurrence")
    if actor is None:
        return None
    occurrence = OpponentLibraryExileOccurrence.from_dict(effect["occurrence"])
    if occurrence.effect_controller != actor:
        raise ValueError("Exile play permission changed its original effect controller")
    return occurrence
