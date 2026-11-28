"""Closed scan and election data for public nonland exile from each library."""
from collections.abc import Mapping

from .library_selection import LibrarySelectionObjectIdentity


ELECTED_NONLAND_EXILE_SCOPE = "each_library_nonland_opponent_election"
ELECTED_NONLAND_EXILE_MECHANIC = "each-library-nonland-election-free-casts"
ELECTED_NONLAND_EXILE_CAPABILITY = "library.exile.each_nonland_opponent_election"
ELECTED_NONLAND_EXILE_VERSION = 1
ELECTED_NONLAND_EXILE_SCAN_VERSION = 2
ELECTION_FIELD = "nonland_election"
SCAN_FIELD = "nonland_scan"
_TURN_TRIGGER = {
    "all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ],
}


def _seat(seat):
    return type(seat) is str and bool(seat) and not seat.startswith("$")


def elected_nonland_exile_instruction():
    from ..fixed_top_exile import FIXED_TOP_EXILE_OPERATION

    return {
        "op": FIXED_TOP_EXILE_OPERATION, "schema_version": ELECTED_NONLAND_EXILE_VERSION,
        "scope": ELECTED_NONLAND_EXILE_SCOPE, "player": "$controller",
    }


def validate_elected_nonland_exile_instruction(effect, *, resolved=False):
    from ..replacement.immutable import thaw_value

    if not isinstance(effect, Mapping):
        raise ValueError("Nonland exile requires a typed instruction")
    expected = elected_nonland_exile_instruction()
    if resolved:
        expected["player"] = effect.get("player")
        if not _seat(expected["player"]):
            raise ValueError("Nonland exile requires the resolving controller")
    if (not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int
            or thaw_value(effect) != expected):
        raise ValueError("Nonland exile requires the original untargeted library instruction")
    return ELECTED_NONLAND_EXILE_SCOPE


def validate_elected_nonland_exile_program(program):
    from ..replacement.immutable import FrozenMap

    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or FrozenMap(program.event_condition or {}) != FrozenMap(_TURN_TRIGGER)):
        raise ValueError("Nonland exile requires its original controller-upkeep trigger")


def captured_nonland_rows(results):
    if not isinstance(results, (tuple, list)):
        raise ValueError("Nonland exile requires an ordered capture set")
    rows = []
    for result in results:
        if (not isinstance(result, Mapping) or set(result) != {"owner", "identity"}
                or not _seat(result["owner"])):
            raise ValueError("Nonland exile capture requires its supplier and incarnation")
        rows.append((result["owner"], LibrarySelectionObjectIdentity.from_dict(result["identity"])))
    if len({identity.object_id for _, identity in rows}) != len(rows):
        raise ValueError("Nonland exile requires distinct committed cards")
    return tuple(rows)


def validate_nonland_scan(effect):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OPERATION

    if (set(effect) != {"op", "schema_version", "scope", "player", "instruction", SCAN_FIELD}
            or effect["op"] != FIXED_TOP_EXILE_OPERATION or effect["scope"] != ELECTED_NONLAND_EXILE_SCOPE
            or type(effect["schema_version"]) is not int or effect["schema_version"] != ELECTED_NONLAND_EXILE_SCAN_VERSION):
        raise ValueError("Invalid nonland scan continuation")
    instruction = effect["instruction"]
    validate_elected_nonland_exile_instruction(instruction, resolved=True)
    if effect["player"] != instruction["player"]:
        raise ValueError("Nonland scan controller changed")
    progress = effect[SCAN_FIELD]
    if not isinstance(progress, Mapping) or set(progress) != {"pending", "results", "completed"}:
        raise ValueError("Nonland scan requires pending tops and committed results")
    pending = captured_nonland_rows(progress["pending"])
    results = captured_nonland_rows(progress["results"])
    done = progress["completed"]
    suppliers = {owner for owner, _ in pending}
    if (not pending or len(suppliers) != len(pending) or not isinstance(done, (list, tuple))
            or not all(_seat(owner) for owner in done) or len(set(done)) != len(done) or suppliers.intersection(done)):
        raise ValueError("A completed library cannot continue its nonland scan")
    return instruction, pending, results, tuple(done)


def is_nonland_election_offer(effect):
    if not isinstance(effect, Mapping):
        return False
    instruction = effect.get("instruction")
    return isinstance(instruction, Mapping) and instruction.get("scope") == ELECTED_NONLAND_EXILE_SCOPE


def nonland_election_offer(instruction, results, *, stage="elect", opponent=None, remaining=None):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION

    return {
        "op": FIXED_TOP_EXILE_OFFER_OPERATION,
        "instruction": instruction,
        "results": [{"owner": owner, "identity": identity.to_dict()} for owner, identity in results],
        ELECTION_FIELD: {"stage": stage, "opponent": opponent, "remaining": remaining},
        **({"player": opponent} if stage == "exclude" else {}),
    }


def validate_nonland_election_offer(effect):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OFFER_OPERATION

    if not is_nonland_election_offer(effect) or effect.get("op") != FIXED_TOP_EXILE_OFFER_OPERATION:
        raise ValueError("Nonland election has no original exile instruction")
    instruction = effect["instruction"]
    validate_elected_nonland_exile_instruction(instruction, resolved=True)
    slot = effect.get(ELECTION_FIELD)
    if not isinstance(slot, Mapping) or set(slot) != {"stage", "opponent", "remaining"}:
        raise ValueError("Nonland election stage has a closed shape")
    stage, opponent, remaining = slot["stage"], slot["opponent"], slot["remaining"]
    expected = {"op", "instruction", "results", ELECTION_FIELD}
    if stage == "exclude":
        expected.add("player")
    if set(effect) != expected:
        raise ValueError("Nonland election continuation has extra or missing fields")
    if stage == "elect":
        valid = opponent is None and remaining is None
    elif stage == "exclude":
        valid = _seat(opponent) and opponent != instruction["player"] and remaining is None and effect["player"] == opponent
    else:
        valid = (stage == "cast" and (opponent is None or (_seat(opponent) and opponent != instruction["player"]))
                 and type(remaining) is int and remaining in (1, 2))
    if not valid:
        raise ValueError("Nonland election lost its actor or exact cast limit")
    return stage, opponent, remaining, captured_nonland_rows(effect["results"])
