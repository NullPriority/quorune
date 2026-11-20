"""Fixed private inspection over one independently declared target library."""
from collections.abc import Mapping
from dataclasses import dataclass

TARGET_LIBRARY_INSPECTION_OPERATION = "inspect_target_library"
TARGET_LIBRARY_INSPECTION_MECHANIC = "fixed-target-library-inspection"
TARGET_LIBRARY_INSPECTION_CAPABILITY = "library.inspect.fixed_target_player"
TARGET_LIBRARY_INSPECTION_FIELDS = ("op", "player", "target", "inspection")


@dataclass(frozen=True, slots=True)
class TargetLibraryInspectionSpec:
    count: int
    mode: str
    opponent_only: bool = False

    def __post_init__(self):
        if (type(self.count) is not int or not 1 <= self.count <= 3
                or type(self.mode) is not str or self.mode not in {"look", "may_graveyard", "one_graveyard", "one_exile"}
                or self.mode == "may_graveyard" and self.count != 1 or type(self.opponent_only) is not bool):
            raise ValueError("Target library inspection requires a closed fixed look or single-card move")

    def to_dict(self):
        return {"schema_version": 1, "count": self.count, "mode": self.mode, "opponent_only": self.opponent_only}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "count", "mode", "opponent_only"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Target library inspection has an explicit closed codec")
        return cls(value["count"], value["mode"], value["opponent_only"])

    def target_schema(self):
        value = {"zones": ["player"], "categories": ["player"], "count": 1}
        if self.opponent_only:value["player_relation"] = "opponent"
        return value

    def effect(self, *, player="$controller", target="$target.0"):
        return {"op": TARGET_LIBRARY_INSPECTION_OPERATION, "player": player, "target": target, "inspection": self.to_dict()}


def decode_target_library_inspection(effect, *, actor=None):
    if (not isinstance(effect, Mapping) or set(effect) != set(TARGET_LIBRARY_INSPECTION_FIELDS)
            or effect["op"] != TARGET_LIBRARY_INSPECTION_OPERATION
            or effect["player"] != (actor if actor is not None else "$controller")):
        raise ValueError("Inspection requires the original resolving actor")
    target = effect["target"]
    if actor is None:
        if target != "$target.0":raise ValueError("Inspection retains the original player target")
    elif target is not None and (type(target) is not str or not target or target.startswith("$")):
        raise ValueError("Inspection requires a bound player target")
    return TargetLibraryInspectionSpec.from_dict(effect["inspection"])
