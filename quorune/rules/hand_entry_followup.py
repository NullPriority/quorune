from __future__ import annotations

"""Versioned follow-up on one successful private hand entry."""

from collections.abc import Mapping

from .reanimation_followup import BattlefieldEntryFollowup


HAND_ENTRY_FOLLOWUP_CAPABILITY = "zone.move.fixed_private_hand_haste_and_sacrifice"
HAND_ENTRY_FOLLOWUP_VERSION = 2
HAND_ENTRY_FOLLOWUP_FIELDS = frozenset({"schema_version", "followup"})


def decode_hand_entry_followup(effect: Mapping) -> BattlefieldEntryFollowup | None:
    fields = HAND_ENTRY_FOLLOWUP_FIELDS.intersection(effect)
    if not fields:
        return None
    if (fields != HAND_ENTRY_FOLLOWUP_FIELDS or type(effect["schema_version"]) is not int
        or effect["schema_version"] != HAND_ENTRY_FOLLOWUP_VERSION):
        raise ValueError("Private hand entry requires its complete versioned follow-up")
    followup = BattlefieldEntryFollowup.from_dict(effect["followup"])
    if (followup.departure, followup.end_step, followup.power_boost) != ("sacrifice", "next", 0):
        raise ValueError("Private hand entry requires a haste grant and one next-end-step sacrifice")
    return followup


def hand_entry_followup_runtime_dependencies(program):
    """Recover the minimum runtime authority from nested effect payloads."""
    from .public_zone_move_capability_shapes import fixed_private_hand_entry_node_capabilities

    required = set()

    def visit(value):
        if isinstance(value, Mapping):
            if value.get("op") == "put_card_from_hand" and HAND_ENTRY_FOLLOWUP_FIELDS.intersection(value):
                owned = fixed_private_hand_entry_node_capabilities(effects=(value,), target_schema=None,
                    mechanic_ids=("fixed-private-hand-entry", "fixed-public-zone-move"))
                if HAND_ENTRY_FOLLOWUP_CAPABILITY not in owned:
                    raise ValueError("Private hand entry lacks its closed follow-up authority")
                required.update(owned)
            for child in value.values():
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit((program.effects, program.target_schema))
    return tuple(sorted(required))
