"""Recognize the complete current-block Equipment keyword grant."""

from . import regex_working_set as re
from .creature_subtypes import canonical_creature_subtype
from ..semantic_runtime.attached_combat_strike import EquippedCombatStrikeHandler, combat_counterpart_query


def equipped_combat_strike_template(text: str):
    match = re.fullmatch(
        r"Equipped creature has first strike as long as it['’]s blocking or blocked by "
        r"an? (?P<first>.+?) or (?P<second>.+?)\.?",
        " ".join(text.split()), re.I,
    )
    if match is None:
        return None
    first = canonical_creature_subtype(match["first"])
    second = canonical_creature_subtype(match["second"])
    if first is None or second is None or first == second:
        return None
    handler = EquippedCombatStrikeHandler()
    query = combat_counterpart_query(tuple(sorted((first, second))))
    return (
        "continuous-equipped-combat-counterpart-first-strike-v1",
        {"handler_id": handler.handler_id, "schema_version": handler.schema_version,
         "event": handler.event, "counterpart": query.to_dict()},
        handler.capability_dependencies,
    )
