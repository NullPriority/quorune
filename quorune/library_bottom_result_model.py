"""Typed single-card library movement with a committed creature-card result."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .replacement.immutable import FrozenMap, thaw_value
from .scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec


LIBRARY_BOTTOM_RESULT_OPERATION = "resolve_library_bottom_creature_result"
LIBRARY_BOTTOM_RESULT_CAPABILITY = "library.move.bottom_committed_creature_result"
LIBRARY_BOTTOM_RESULT_MECHANIC = "library-bottom-committed-creature-result"
LIBRARY_BOTTOM_SOURCE_POWER = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, "power")
LIBRARY_BOTTOM_RESULT_RULES = (
    "107.2", "113.7a", "115.1c", "400.7j", "404.1", "608.2c", "608.2h", "614.1", "701.17a", "702.26b", "702.26d",
)


class LibraryBottomResultKind(StrEnum):
    TARGET_PLAYER_CREATURE_TOKEN = "target_player_creature_token"
    CONTROLLER_SOURCE_POWER_ENTRY = "controller_source_power_entry"


@dataclass(frozen=True, slots=True)
class LibraryBottomResultSpec:
    kind: LibraryBottomResultKind
    token: FrozenMap | None = None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Library-bottom result requires schema version one")
        if not isinstance(self.kind, LibraryBottomResultKind):
            raise ValueError("Library-bottom result requires a represented outcome")
        if self.kind is LibraryBottomResultKind.TARGET_PLAYER_CREATURE_TOKEN:
            if not isinstance(self.token, FrozenMap):
                raise ValueError("A creature-token result requires its typed token instruction")
        elif self.token is not None:
            raise ValueError("Source-relative creature entry cannot create a token")

    @property
    def targets_player(self):
        return self.kind is LibraryBottomResultKind.TARGET_PLAYER_CREATURE_TOKEN

    def target_schema(self):
        return {"zones": ["player"], "categories": ["player"], "count": 1} if self.targets_player else None

    def to_effect(self):
        result = {"kind": self.kind.value}
        if self.targets_player:
            result["token"] = thaw_value(self.token)
        else:
            result["source_power"] = LIBRARY_BOTTOM_SOURCE_POWER.to_dict()
        return {
            "op": LIBRARY_BOTTOM_RESULT_OPERATION,
            "schema_version": self.schema_version,
            "player": "$target.0" if self.targets_player else "$controller",
            "result": result,
        }

    @classmethod
    def from_effect(cls, value, *, actor=None):
        if (not isinstance(value, Mapping) or set(value) != {"op", "schema_version", "player", "result"}
                or value["op"] != LIBRARY_BOTTOM_RESULT_OPERATION):
            raise ValueError("Library-bottom result fields are closed")
        result = value["result"]
        if not isinstance(result, Mapping) or "kind" not in result:
            raise ValueError("Library-bottom movement requires one typed committed result")
        kind = LibraryBottomResultKind(result["kind"])
        if kind is LibraryBottomResultKind.TARGET_PLAYER_CREATURE_TOKEN:
            if set(result) != {"kind", "token"} or not isinstance(result["token"], Mapping):
                raise ValueError("Library-bottom token result fields are closed")
            spec = cls(kind, FrozenMap(result["token"]), value["schema_version"])
        else:
            if (set(result) != {"kind", "source_power"}
                    or ScalarEffectAmountSpec.from_dict(result["source_power"]) != LIBRARY_BOTTOM_SOURCE_POWER):
                raise ValueError("Library-bottom creature entry requires the original source's current power")
            spec = cls(kind, schema_version=value["schema_version"])
        player = value["player"]
        if actor is None:
            if player != ("$target.0" if spec.targets_player else "$controller"):
                raise ValueError("Library-bottom operation changed the printed library role")
        elif spec.targets_player:
            if player is not None and (type(player) is not str or not player or player.startswith("$")):
                raise ValueError("Library-bottom operation requires its resolved target player")
        elif player != actor:
            raise ValueError("Library-bottom creature entry requires the ability controller's library")
        return spec



def library_bottom_uses_source_power(effects):
    return any(
        effect.get("op") == LIBRARY_BOTTOM_RESULT_OPERATION
        and isinstance(effect.get("result"), Mapping)
        and effect["result"].get("kind") == LibraryBottomResultKind.CONTROLLER_SOURCE_POWER_ENTRY
        for effect in effects
    )
