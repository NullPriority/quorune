from __future__ import annotations

from collections.abc import Mapping


HANDLER_ID = "continuous.characteristics.linked-exile-creature.v1"
CAPABILITY = "continuous.characteristics.linked_exile_creature"
TYPE_OPERATION = "set_linked_exile_creature_subtypes"
STATS_OPERATION = "set_linked_exile_creature_stats"
OPERATIONS = frozenset({TYPE_OPERATION, STATS_OPERATION})


def linked_exile_characteristic_candidates(value):
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("A linked setting requires at least one typed exile incarnation")
    expected = {"object_id", "ref", "logical_object_id"}
    if any(not isinstance(row, Mapping) or set(row) != expected
           or any(type(member) is not str or not member for member in row.values()) for row in value):
        raise ValueError("Linked exile candidates require complete object identities")
    if len({row["object_id"] for row in value}) != len(value):
        raise ValueError("A linked setting cannot repeat an exile incarnation")
    return tuple(value)
