"""Closed instructions and public incarnations for casts from fixed library tops."""

from collections.abc import Mapping

from .rules.library_selection import LibrarySelectionObjectIdentity


FIXED_TOP_EXILE_OPERATION = "exile_fixed_library_tops_and_cast"
FIXED_TOP_EXILE_OFFER_OPERATION = "offer_fixed_top_exile_cast"
FIXED_TOP_EXILE_MECHANIC = "fixed-top-exile-resolving-cast"
FIXED_TOP_EXILE_CAPABILITY = "zone.exile.fixed_top_resolving_cast"
FIXED_TOP_EXILE_RULES = ("400.7j", "601.2", "608.2c", "608.2d", "608.2g", "712.8a", "712.11")
FIXED_TOP_EXILE_DAMAGE_CAPABILITIES = (
    "damage.amount.positive", "damage.result.player_life", "damage.source.stack_current_or_lki",
)


def _seat(value):
    return type(value) is str and bool(value) and not value.startswith("$")


def validate_fixed_top_exile(effect, *, resolved=False):
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


def _valid_decline_damage(value, *, resolved):
    fields = {"op", "source", "amount"}
    return (isinstance(value, Mapping) and set(value) in ((fields, fields | {"source_snapshot"}) if resolved else (fields,))
            and value["op"] == "damage_each_opponent" and type(value["amount"]) is int and value["amount"] > 0
            and (_seat(value["source"]) if resolved else value["source"] == "$source"))


def fixed_top_exile_dependencies(effect):
    if effect.get("op") != FIXED_TOP_EXILE_OPERATION:
        return ()
    scope = validate_fixed_top_exile(effect)
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
    if (len({owner for owner, _ in results}) != len(results)
            or len({identity.object_id for _, identity in results}) != len(results)):
        raise ValueError("Fixed top exile results require one distinct card per library")
    owner = instruction["supplier"] if scope == "cast_event_library" else instruction["player"]
    if scope != "each_library" and any(supplier != owner for supplier, _ in results):
        raise ValueError("Fixed top exile result came from an unrepresented library")
    return tuple(results)
