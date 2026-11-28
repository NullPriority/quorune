"""Cast access derived from a committed face-up conditional exile incarnation."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..linked_exile_return_model import ExiledObjectIdentity


CAPABILITY = "permission.exile.conditional_incarnation_cast"
FIELD = "conditional_exile_cast"
DURATION = "conditional_exile_cast_incarnation"


def grant_conditional_exile_cast(host, actor, objects):
    if actor not in host.active_seats:
        raise GameRuleError("Conditional exile casting requires its original resolving controller")
    for raw in objects:
        identity = ExiledObjectIdentity.from_dict(raw)
        card = host.state.cards.get(identity.object_id)
        if identity.matches_exiled_card(card) and card.is_card_object and not card.face_down:
            card.annotations[FIELD] = {"schema_version": 1, "player": actor, "identity": identity.to_dict()}


def current_conditional_exile_cast(state, seat, card):
    value = card.annotations.get(FIELD)
    player = state.players.get(seat)
    if (card.zone != "exile" or not card.is_card_object or card.face_down
            or player is None or not player.in_game or not isinstance(value, Mapping)
            or set(value) != {"schema_version", "player", "identity"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["player"] != seat):
        return None
    try:
        identity = ExiledObjectIdentity.from_dict(value["identity"])
    except ValueError:
        return None
    if not identity.matches_exiled_card(card):
        return None
    return {
        "player": seat, "zone": "exile", "without_mana_cost": False, "allow_land": False,
        "allow_spell": True, "duration": DURATION, "allow_any_mana_type": True,
    }
