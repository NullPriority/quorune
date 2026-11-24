"""A fixed private draw for each former controller from one committed exile."""

from .committed_quantity_followups import CommittedQuantityFollowup
from ..replacement.immutable import FrozenMap
from ..target_cardinality import validate_selected_target_count


OPPONENT_EXILE_DRAW_FIELD = "opponent_exile_draw_version"
OPPONENT_EXILE_DRAW_CAPABILITY = "permanent.exile.per_opponent_draw_result"


def opponent_exile_draw_payload_dependencies(effect):
    if OPPONENT_EXILE_DRAW_FIELD not in effect:
        return ()
    version = effect[OPPONENT_EXILE_DRAW_FIELD]
    if (
        type(version) is not int or version != 1
        or effect.get("op") != "exile_permanent_targets"
        or effect.get("cards") != "$targets"
        or effect.get("announced_target_count") is not True
        or set(effect) != {"op", "cards", "maximum_targets", "announced_target_count", OPPONENT_EXILE_DRAW_FIELD}
    ):
        raise ValueError("Opponent exile draws require their exact announced exile instruction")
    validate_selected_target_count(effect["maximum_targets"])
    return (OPPONENT_EXILE_DRAW_CAPABILITY, "zone.draw.library_to_hand", "zone.change.destination_replacement")


def opponent_exile_draw_followup(controllers) -> CommittedQuantityFollowup:
    if not controllers:
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        "op": "draw_each_player", "players": tuple(controllers), "count": 1, "private": True,
    }),))
