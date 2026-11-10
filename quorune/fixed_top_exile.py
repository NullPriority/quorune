"""Closed instructions and public incarnations for plays from fixed library tops."""

from collections.abc import Mapping
from dataclasses import dataclass

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
RESOLVING_TOP_PLAY_SCOPE = "controller_resolving_play"
RESOLVING_TOP_PLAY_MECHANIC = "fixed-top-resolving-play"
RESOLVING_TOP_PLAY_CAPABILITY = "zone.exile.fixed_top_resolving_play"
OWN_LIBRARY_EXILE_SCOPES = frozenset({CHOSEN_IMPULSE_SCOPE, RESOLVING_TOP_PLAY_SCOPE})
_CHOSEN_DURATIONS = {ImpulseAccessDuration.END_OF_TURN.value, ImpulseAccessDuration.END_OF_NEXT_TURN.value}
FIXED_TOP_EXILE_MECHANIC_CAPABILITIES = {
    FIXED_TOP_EXILE_MECHANIC: (FIXED_TOP_EXILE_CAPABILITY,),
    CHOSEN_IMPULSE_MECHANIC: (CHOSEN_IMPULSE_CAPABILITY,),
    RESOLVING_TOP_PLAY_MECHANIC: (RESOLVING_TOP_PLAY_CAPABILITY,),
}
FIXED_TOP_EXILE_RULES = ("400.7j", "601.2", "608.2c", "608.2d", "608.2g", "712.8a", "712.11")
FIXED_TOP_EXILE_DAMAGE_CAPABILITIES = (
    "damage.amount.positive", "damage.result.player_life", "damage.source.stack_current_or_lki",
)


def _seat(value):
    return type(value) is str and bool(value) and not value.startswith("$")


@dataclass(frozen=True, slots=True)
class ResolvingExileLandPlayIntent:
    actor: str
    stack_ref: str
    card: LibrarySelectionObjectIdentity
    option_id: str
    replacement_selections: tuple = ()

    def __post_init__(self):
        if (not _seat(self.actor) or not _seat(self.stack_ref) or not _seat(self.option_id)
                or not isinstance(self.card, LibrarySelectionObjectIdentity)
                or not isinstance(self.replacement_selections, tuple)):
            raise ValueError("Resolving land play requires a current option, card, and stack identity")

    def identity(self):
        return {"schema_version": 1, "actor": self.actor, "stack_ref": self.stack_ref,
                "card": self.card.to_dict(), "option_id": self.option_id}


def validate_resolving_land_play_identity(value):
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "actor", "stack_ref", "card", "option_id"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("Resolving land play continuation identity is malformed")
    return ResolvingExileLandPlayIntent(value["actor"], value["stack_ref"],
        LibrarySelectionObjectIdentity.from_dict(value["card"]), value["option_id"]).identity()


def validate_fixed_top_exile(effect, *, resolved=False):
    if isinstance(effect, Mapping) and effect.get("scope") == CHOSEN_IMPULSE_SCOPE:
        return _validate_chosen_impulse(effect, resolved=resolved)
    if isinstance(effect, Mapping) and effect.get("scope") == RESOLVING_TOP_PLAY_SCOPE:
        return _validate_resolving_top_play(effect, resolved=resolved)
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


def _validate_resolving_top_play(effect, *, resolved):
    if (set(effect) != {"op", "schema_version", "player", "scope", "count"}
            or effect["op"] != FIXED_TOP_EXILE_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 3
            or not (_seat(effect["player"]) if resolved else effect["player"] == "$controller")
            or type(effect["count"]) is not int or effect["count"] != 2):
        raise ValueError("Resolving top play requires a closed two-card own-library exile")
    return RESOLVING_TOP_PLAY_SCOPE


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
    if scope == RESOLVING_TOP_PLAY_SCOPE:
        return (FIXED_TOP_EXILE_CAPABILITY, RESOLVING_TOP_PLAY_CAPABILITY)
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
    if scope in OWN_LIBRARY_EXILE_SCOPES:
        if len(results) > instruction["count"]:
            raise ValueError("Fixed top exile captured too many cards")
    elif len({owner for owner, _ in results}) != len(results):
        raise ValueError("Fixed top exile results require one distinct card per library")
    owner = instruction["supplier"] if scope == "cast_event_library" else instruction["player"]
    if scope != "each_library" and any(supplier != owner for supplier, _ in results):
        raise ValueError("Fixed top exile result came from an unrepresented library")
    return tuple(results)
