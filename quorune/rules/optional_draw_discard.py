from __future__ import annotations

"""Closed optional draw instruction followed by mandatory controller discard."""

from typing import Mapping
from .affected_player_discard_capability_shapes import (
    fixed_affected_player_discard_node_capabilities, fixed_controller_discard_node_capabilities,
)


def optional_draw_discard_is_closed(effects,*,player=None):
    if not isinstance(effects,(list,tuple)) or len(effects)!=2 or any(not isinstance(e,Mapping) for e in effects):return False
    draw,discard=effects
    if set(draw)!={'op','player','count','private'} or draw['op']!='draw' or draw['private'] is not True or type(draw['count']) is not int or not 1<=draw['count']<=3:return False
    if player is not None and draw['player']!=player:return False
    return bool(_controller_discard_dependencies(discard, draw["player"]))


def _controller_discard_dependencies(discard, player, *, requires_fixed_controller=False):
    players = discard.get("players")
    if discard.get("actor") != player or not isinstance(players, (list, tuple)) or list(players) != [player]:
        return ()
    # Reuse the canonical typed private discard shape, with its fixed controller
    # placeholder restored after seat binding for representation validation.
    restored = {**discard, "actor": "$controller", "players": ["$controller"]}
    if requires_fixed_controller:
        return fixed_controller_discard_node_capabilities(
            effects=(restored,), target_schema=None,
            mechanic_ids=("fixed-controller-card-discard", "fixed-affected-player-discard", "cr-402-hand"),
        )
    return fixed_affected_player_discard_node_capabilities(
        effects=(restored,), target_schema=None,
        mechanic_ids=("fixed-affected-player-discard", "cr-402-hand"), allow_controller=True,
    )


SOURCE_SHARED_TYPE_DRAW_DISCARD_VERSION = 2
ENTRY_EXEMPT_DRAW_DISCARD_VERSION = 8


def is_entry_exempt_draw_discard(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "offer_optional_effect"
            and effect.get("schema_version") == ENTRY_EXEMPT_DRAW_DISCARD_VERSION)


def validate_entry_exempt_draw_discard(effect, *, actor=None):
    """Check both printed outcomes, or one materialized draw-only outcome."""
    player = "$controller" if actor is None else actor
    if (not is_entry_exempt_draw_discard(effect)
            or set(effect) != {"op", "player", "effects", "schema_version"}
            or type(effect["schema_version"]) is not int or effect["player"] != player):
        raise ValueError("Entry-exempt discard requires the exact optional controller choice")
    effects = effect["effects"]
    if (not isinstance(effects, (list, tuple)) or len(effects) not in ({2} if actor is None else {1, 2})
            or any(not isinstance(child, Mapping) for child in effects)):
        raise ValueError("Entry-exempt discard requires fixed draw and discard instructions")
    draw = effects[0]
    if (dict(draw) != {"op": "draw", "player": player, "count": 1, "private": True}
            or type(draw["count"]) is not int or draw["private"] is not True):
        raise ValueError("Entry-exempt discard requires one private controller draw")
    if len(effects) == 2:
        discard = effects[1]
        if (type(discard.get("count")) is not int or discard["count"] != 1
                or not _controller_discard_dependencies(discard, player, requires_fixed_controller=True)):
            raise ValueError("Entry-exempt discard requires one private controller discard")
    return effects


def is_source_shared_type_draw_discard(value):
    return (isinstance(value, Mapping) and value.get("op") == "offer_optional_effect"
            and value.get("schema_version") == SOURCE_SHARED_TYPE_DRAW_DISCARD_VERSION)


def validate_source_shared_type_draw_discard(value, *, actor=None):
    from ..query_effect_amount_model import PublicQueryAmountSpec, source_shared_type_amount_spec

    player = "$controller" if actor is None else actor
    effects = value.get("effects") if isinstance(value, Mapping) else None
    if (not is_source_shared_type_draw_discard(value)
            or set(value) != {"op", "player", "effects", "schema_version"}
            or type(value["schema_version"]) is not int or value["player"] != player
            or not isinstance(effects, (tuple, list)) or len(effects) != 2
            or any(not isinstance(effect, Mapping) for effect in effects)):
        raise ValueError("Shared source draw requires its exact versioned controller choice")
    draw, discard = effects
    if (set(draw) != {"op", "player", "count", "private"} or draw["op"] != "draw"
            or draw["private"] is not True or draw["player"] != player):
        raise ValueError("Shared source draw requires the one private controller draw")
    if actor is None:
        if PublicQueryAmountSpec.from_dict(draw["count"]) != source_shared_type_amount_spec():
            raise ValueError("Shared source draw requires the closed source-relative count")
    elif type(draw["count"]) is not int or draw["count"] < 0:
        raise ValueError("Shared source draw requires a resolved nonnegative count")
    if type(discard.get("count")) is not int or discard["count"] != 1:
        raise ValueError("Applying shared source draw requires discarding one card")
    dependencies = _controller_discard_dependencies(discard, player, requires_fixed_controller=True)
    if not dependencies:
        raise ValueError("Shared source draw requires its exact private discard result")
    return dependencies


def source_shared_type_draw_discard_dependencies(value):
    if not is_source_shared_type_draw_discard(value):
        return ()
    from ..query_effect_amount_model import public_query_amount_runtime_dependencies

    discard = validate_source_shared_type_draw_discard(value)
    draw = public_query_amount_runtime_dependencies(value["effects"][0]["count"])
    return tuple(sorted({"effect.choice.optional_fixed", "zone.draw.library_to_hand", *discard, *draw}))
