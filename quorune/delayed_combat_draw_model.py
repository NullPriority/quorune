"""Validate the saved combat-damage watch on the delayed-trigger record."""

WATCHED_COMBAT_DRAW_FIELD = "watched_combat_damage_draw"


def validate_watched_combat_draw_trigger(trigger):
    condition = trigger.condition
    if (
        set(condition) != {WATCHED_COMBAT_DRAW_FIELD, "combat", "source_object_id", "source_logical_object_id"}
        or type(condition[WATCHED_COMBAT_DRAW_FIELD]) is not int or condition[WATCHED_COMBAT_DRAW_FIELD] != 1
        or condition["combat"] is not True or trigger.event_kind != "damage.dealt" or trigger.once is not False
        or type(trigger.expires_turn_sequence) is not int or trigger.expires_turn_sequence != trigger.created_turn_sequence
        or any(type(value) is not str or not value for value in (
            trigger.source_object_id, trigger.source_logical_object_id,
            condition["source_object_id"], condition["source_logical_object_id"],
        ))
        or trigger.source_object_id == condition["source_object_id"]
    ):
        raise ValueError("A combat draw watch requires separate spell and creature identities and this turn's duration")
