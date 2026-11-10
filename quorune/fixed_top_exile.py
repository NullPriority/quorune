"""Closed instructions and public incarnations for casts from fixed library tops."""

from collections.abc import Mapping

from .impulse_access_model import ImpulseAccessDuration
from .rules.library_selection import LibrarySelectionObjectIdentity


FIXED_TOP_EXILE_OPERATION = "exile_fixed_library_tops_and_cast"
FIXED_TOP_EXILE_OFFER_OPERATION = "offer_fixed_top_exile_cast"
FIXED_TOP_EXILE_MECHANIC = "fixed-top-exile-resolving-cast"
FIXED_TOP_EXILE_CAPABILITY = "zone.exile.fixed_top_resolving_cast"
CHOSEN_IMPULSE_SCOPE = "controller_chosen_impulse"
CHOSEN_IMPULSE_MECHANIC = "chosen-top-impulse-access"
CHOSEN_IMPULSE_CAPABILITY = "zone.impulse_access.chosen_top_exile"
CHOSEN_IMPULSE_GRANT_FIELD = "chosen_impulse_access"
_CHOSEN_DURATIONS = {ImpulseAccessDuration.END_OF_TURN.value, ImpulseAccessDuration.END_OF_NEXT_TURN.value}
FIXED_TOP_EXILE_MECHANIC_CAPABILITIES = {
    FIXED_TOP_EXILE_MECHANIC: (FIXED_TOP_EXILE_CAPABILITY,),
    CHOSEN_IMPULSE_MECHANIC: (CHOSEN_IMPULSE_CAPABILITY,),
}
FIXED_TOP_EXILE_RULES = ("400.7j", "601.2", "608.2c", "608.2d", "608.2g", "712.8a", "712.11")
FIXED_TOP_EXILE_DAMAGE_CAPABILITIES = (
    "damage.amount.positive", "damage.result.player_life", "damage.source.stack_current_or_lki",
)


def _seat(value):
    return type(value) is str and bool(value) and not value.startswith("$")


def validate_fixed_top_exile(effect, *, resolved=False):
    if isinstance(effect, Mapping) and effect.get("scope") == CHOSEN_IMPULSE_SCOPE:
        return _validate_chosen_impulse(effect, resolved=resolved)
    fields = {"op", "schema_version", "player", "scope", "supplier", "decline"}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != FIXED_TOP_EXILE_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or not (_seat(effect["player"]) if resolved else effect["player"] == "$controller")):
        raise ValueError("Fixed top exile requires its closed resolving instruction")
    scope = effect["scope"]
    supplier = effect["supplier"]
    decline = effect["decline"]
    if scope == "each_library":
        valid = supplier is None and decline is None
    elif scope == "cast_event_library":
        valid = (_seat(supplier) and supplier != effect["player"] if resolved else supplier == "$context.player") and decline is None
    elif scope == "controller_library":
        valid = supplier is None and _valid_decline_damage(decline, resolved=resolved)
    else:
        valid = False
    if not valid:
        raise ValueError("Fixed top exile has an invalid library, casting or decline role")
    return scope


def _validate_chosen_impulse(effect, *, resolved):
    if (set(effect) != {"op", "schema_version", "player", "scope", "count", "duration"}
            or effect["op"] != FIXED_TOP_EXILE_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 2
            or not (_seat(effect["player"]) if resolved else effect["player"] == "$controller")
            or type(effect["count"]) is not int or not 2 <= effect["count"] <= 10
            or type(effect["duration"]) is not str or effect["duration"] not in _CHOSEN_DURATIONS):
        raise ValueError("Chosen impulse access requires a fixed own-library exile and a bounded play window")
    return CHOSEN_IMPULSE_SCOPE


def chosen_impulse_grant(effect, *, actor):
    fields = {"op", "schema_version", "player", CHOSEN_IMPULSE_GRANT_FIELD}
    if (not isinstance(effect, Mapping) or set(effect) != fields or effect["op"] != "grant_cast_permission"
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or not _seat(actor) or effect["player"] != actor):
        raise ValueError("Chosen impulse access grant requires its resolving controller")
    payload = effect[CHOSEN_IMPULSE_GRANT_FIELD]
    if (not isinstance(payload, Mapping) or set(payload) != {"identity", "duration"}
            or type(payload["duration"]) is not str or payload["duration"] not in _CHOSEN_DURATIONS):
        raise ValueError("Chosen impulse access grant requires its captured exile identity and duration")
    return LibrarySelectionObjectIdentity.from_dict(payload["identity"]), ImpulseAccessDuration(payload["duration"])


def _valid_decline_damage(value, *, resolved):
    fields = {"op", "source", "amount"}
    return (isinstance(value, Mapping) and set(value) in ((fields, fields | {"source_snapshot"}) if resolved else (fields,))
            and value["op"] == "damage_each_opponent" and type(value["amount"]) is int and value["amount"] > 0
            and (_seat(value["source"]) if resolved else value["source"] == "$source"))


def fixed_top_exile_dependencies(effect):
    if effect.get("op") != FIXED_TOP_EXILE_OPERATION:
        return ()
    scope = validate_fixed_top_exile(effect)
    if scope == CHOSEN_IMPULSE_SCOPE:
        return (FIXED_TOP_EXILE_CAPABILITY, CHOSEN_IMPULSE_CAPABILITY, "zone.impulse_access.fixed")
    return (FIXED_TOP_EXILE_CAPABILITY, *(
        FIXED_TOP_EXILE_DAMAGE_CAPABILITIES if scope == "controller_library" else ()))


def fixed_top_exile_result_rows(effect):
    fields = {"op", "instruction", "results"}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != FIXED_TOP_EXILE_OFFER_OPERATION or not isinstance(effect["results"], (tuple, list))):
        raise ValueError("Fixed top exile offer requires its captured result set")
    instruction = effect["instruction"]
    scope = validate_fixed_top_exile(instruction, resolved=True)
    results = []
    for row in effect["results"]:
        if not isinstance(row, Mapping) or set(row) != {"owner", "identity"} or not _seat(row["owner"]):
            raise ValueError("Fixed top exile result has an invalid supplier or incarnation")
        results.append((row["owner"], LibrarySelectionObjectIdentity.from_dict(row["identity"])))
    if len({identity.object_id for _, identity in results}) != len(results):
        raise ValueError("Fixed top exile results require distinct cards")
    if scope == CHOSEN_IMPULSE_SCOPE:
        if len(results) > instruction["count"]:
            raise ValueError("Chosen impulse access captured too many cards")
    elif len({owner for owner, _ in results}) != len(results):
        raise ValueError("Fixed top exile results require one distinct card per library")
    owner = instruction["supplier"] if scope == "cast_event_library" else instruction["player"]
    if scope != "each_library" and any(supplier != owner for supplier, _ in results):
        raise ValueError("Fixed top exile result came from an unrepresented library")
    return tuple(results)
