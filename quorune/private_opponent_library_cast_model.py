"""Closed private opponent-library selection and incarnation-bound cast access."""

from collections.abc import Mapping
from dataclasses import dataclass

from .linked_library_imprint_model import library_imprint_selection_effect
from .replacement.immutable import FrozenMap, thaw_value


PRIVATE_OPPONENT_LIBRARY_CAST_SELECTION_VERSION = 25
DAMAGE_LIBRARY_CAST_SELECTION_VERSION = 27
DAMAGE_LIBRARY_CAST_CAPABILITY = "zone.exile.combat_damage_opponent_library_cast"
DAMAGE_LIBRARY_CAST_MECHANIC = "private-damage-opponent-library-exile-cast"
DAMAGE_LIBRARY_CAST_PLAYER = "$context.target"
DAMAGE_SELECTED = "_damage_library_selected"
DAMAGE_GRAVE_ORDER = "_damage_library_grave_order"
PRIVATE_OPPONENT_LIBRARY_CAST_CAPABILITY = "zone.exile.private_opponent_library_cast"
PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC = "private-opponent-library-exile-cast"
PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION = "exile_face_down_cast"
PRIVATE_OPPONENT_LIBRARY_CAST_DURATION = "while_in_exile"

_PERMISSION_FIELD = "temporary_play_permission"
_PERMISSION_FIELDS = {
    "schema_version", "duration", "player", "zone", "source", "logical_object_id",
    "without_mana_cost", "allow_spell", "allow_land",
}


@dataclass(frozen=True, slots=True)
class PrivateLibrarySearchPlaySource:
    """The exact selected library card and resolving search that authorize play."""

    stack_ref: str
    library_owner: str
    logical_object_id: str

    def __post_init__(self):
        if any(type(value) is not str or not value or value.startswith("$")
               for value in (self.stack_ref, self.library_owner, self.logical_object_id)):
            raise ValueError("Private library play requires the resolving search and selected incarnation")

    def permits_move(self, *, card, viewer, stack):
        item = next((item for item in stack if item.ref == self.stack_ref), None)
        return bool(
            viewer is not None and viewer != self.library_owner and self.library_owner == card.owner
            and card.logical_object_id == self.logical_object_id and item is not None
            and item.kind in {"spell", "spell_copy"} and item.controller == viewer
            and item.context.get("currently_resolving") is True
            and item.context.get("targets_revalidated") is True and tuple(item.targets) == (self.library_owner,)
        )


def private_opponent_library_cast_selection(player="$target.0"):
    return {
        **library_imprint_selection_effect(player),
        "schema_version": PRIVATE_OPPONENT_LIBRARY_CAST_SELECTION_VERSION,
        "selected_destination": PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION,
        "remainder_order": "random",
    }


def damage_library_cast_selection(player=DAMAGE_LIBRARY_CAST_PLAYER):
    return {**private_opponent_library_cast_selection(player),
            "schema_version": DAMAGE_LIBRARY_CAST_SELECTION_VERSION, "look_count": 3,
            "remainder_destination": "graveyard", "remainder_order": "chosen"}


def uses_damage_library_cast_selection(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "fixed_library_selection"
            and effect.get("schema_version") == DAMAGE_LIBRARY_CAST_SELECTION_VERSION)


def uses_private_opponent_library_cast_selection(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "fixed_library_selection" and (
        effect.get("schema_version") == PRIVATE_OPPONENT_LIBRARY_CAST_SELECTION_VERSION
        or effect.get("selected_destination") == PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION
    )


def validate_private_opponent_library_cast_selection(effect, *, player="$target.0"):
    if not isinstance(effect, Mapping) or type(player) is not str or not player:
        raise ValueError("Private opponent library selection requires its original supplier")
    if uses_damage_library_cast_selection(effect):
        expected = damage_library_cast_selection(DAMAGE_LIBRARY_CAST_PLAYER if player == "$target.0" else player)
    else:
        expected = private_opponent_library_cast_selection(player)
    if (set(effect) != set(expected)
            or any(type(effect[field]) is not int for field in ("schema_version", "look_count", "minimum", "maximum"))
            or effect["public_reveal"] is not False or effect["selected_reveal"] is not False
            or thaw_value(FrozenMap(effect)) != expected):
        if uses_damage_library_cast_selection(effect):
            raise ValueError("Private top-three library selection changed its original window or graveyard remainder")
        raise ValueError("Private opponent library selection needs one of the original top four and a random remainder")
    return effect


def private_opponent_library_cast_permission(card, *, actor, source_stack_ref, normal_play=False):
    if (card.zone != "exile" or not card.is_card_object or type(actor) is not str or not actor
            or actor == card.owner or type(source_stack_ref) is not str or not source_stack_ref
            or type(normal_play) is not bool):
        raise ValueError("Private library cast permission requires the resolving viewer and actual exile")
    fields = {
        "schema_version": 2 if normal_play else 1,
        "duration": PRIVATE_OPPONENT_LIBRARY_CAST_DURATION,
        "player": actor,
        "zone": "exile",
        "source": source_stack_ref,
        "logical_object_id": card.logical_object_id,
        "without_mana_cost": False,
        "allow_spell": True,
        "allow_land": normal_play,
    }
    if normal_play:
        fields["permission_kind"] = "private_targeted_library_search"
    return fields


def has_private_opponent_library_cast_designation(card):
    permission = card.annotations.get(_PERMISSION_FIELD)
    return (card.zone == "exile" and isinstance(permission, Mapping)
            and permission.get("duration") == PRIVATE_OPPONENT_LIBRARY_CAST_DURATION)


def private_opponent_library_cast_is_current(state, actor, card, permission):
    if (not isinstance(permission, Mapping) or "schema_version" not in permission
            or type(permission["schema_version"]) is not int or permission["schema_version"] not in {1, 2}
            or set(permission) != (_PERMISSION_FIELDS | {"permission_kind"} if permission["schema_version"] == 2
                                   else _PERMISSION_FIELDS)
            or permission["duration"] != PRIVATE_OPPONENT_LIBRARY_CAST_DURATION
            or permission["logical_object_id"] != card.logical_object_id
            or permission["player"] != actor or actor not in state.players
            or not state.players[actor].in_game):
        return False
    physical = state.cards.get(card.object_id)
    if (physical is None or physical.zone != "exile" or physical.logical_object_id != card.logical_object_id
            or physical.annotations.get(_PERMISSION_FIELD) != permission):
        return False
    try:
        expected = private_opponent_library_cast_permission(
            physical, actor=actor, source_stack_ref=permission["source"],
            normal_play=permission["schema_version"] == 2,
        )
    except ValueError:
        return False
    return (permission == expected and permission["without_mana_cost"] is False
            and permission["allow_spell"] is True and permission["allow_land"] is expected["allow_land"])


def has_private_opponent_library_cast_access(host, *, actor, card):
    return private_opponent_library_cast_is_current(host.state, actor, card, card.annotations.get(_PERMISSION_FIELD))


def has_private_opponent_library_mana_waiver(host, *, actor, card):
    permission = card.annotations.get(_PERMISSION_FIELD)
    return (private_opponent_library_cast_is_current(host.state, actor, card, permission)
            and permission["schema_version"] == 1)


def private_opponent_library_exile_viewer(state, card):
    permission = card.annotations.get(_PERMISSION_FIELD)
    if isinstance(permission, Mapping):
        viewer = permission.get("player")
        if isinstance(viewer, str) and private_opponent_library_cast_is_current(state, viewer, card, permission):
            return viewer
    return None
