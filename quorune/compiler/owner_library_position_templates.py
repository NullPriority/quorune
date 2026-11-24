"""Lower an owner's top-or-bottom choice for a single targeted permanent."""

from . import regex_working_set as re
from .direct_target import direct_permanent_target_spec
from ..public_zone_moves import (
    FIXED_OWNER_ZONE_MOVE_MECHANIC, OWNER_LIBRARY_POSITION_MECHANIC, OWNER_LIBRARY_POSITION_OPERATION,
)
from ..target_controller_information import TARGET_OWNER_INFORMATION_MECHANIC, target_owner_reference
from ..rules.public_zone_move_capability_shapes import fixed_owner_zone_move_node_capabilities


_RESULT = re.compile(
    r"(?:(?P<possessive>target [^.]+?)'s owner|the owner of (?P<direct>target [^.]+?)) "
    r"puts it on their choice of the top or (?:the )?bottom of their library\.",
    re.IGNORECASE,
)


def owner_library_position_template(text):
    body = " ".join(text.replace("’", "'").strip().split())
    match = _RESULT.fullmatch(body)
    if match is None:
        return None
    target = direct_permanent_target_spec(match["possessive"] or match["direct"])
    if target is None:
        return None
    schema = target.to_target_schema()
    if schema.get("zones") != ["battlefield"]:
        return None
    mechanics = (FIXED_OWNER_ZONE_MOVE_MECHANIC, "fixed-public-zone-move", "cr-115-targets")
    for position in ("top", "bottom"):
        effect = {"op": "move", "card": "$target.0", "destination": "library", "position": position}
        if not fixed_owner_zone_move_node_capabilities(effects=(effect,), target_schema=schema, mechanic_ids=mechanics):
            return None
    choice = {
        "op": OWNER_LIBRARY_POSITION_OPERATION, "schema_version": 1,
        "card": "$target.0", "player": target_owner_reference(0),
    }
    return "fixed-owner-library-position-choice-v1", (choice,), schema, (*mechanics, OWNER_LIBRARY_POSITION_MECHANIC, TARGET_OWNER_INFORMATION_MECHANIC)
