"""The five-pawprint budget and a separate slot for each chosen mode instance."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from itertools import product

from .target_controller_reference import (
    TARGET_CONTROLLER_INFORMATION_KIND, controller_reference_index, target_controller_reference,
)


PAWPRINT_FIELD = "pawprint_modes"
PAWPRINT_CAPABILITY = "casting.modal.pawprint_budget"
PAWPRINT_MECHANIC = "fixed-pawprint-mode-budget"
PAWPRINT_RESULT_MODES = "pawprint_selected_modes"
PAWPRINT_SELECTION_FIELD = "pawprint_instances"


@dataclass(frozen=True, slots=True)
class PawprintModeBudget:
    mode_ids: tuple[str, ...] = ("mode_1", "mode_2", "mode_3")
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.mode_ids, (list, tuple))
                or tuple(self.mode_ids) != ("mode_1", "mode_2", "mode_3")):
            raise ValueError("Pawprint modes must retain their three ordered printed branches")
        object.__setattr__(self, "mode_ids", tuple(self.mode_ids))

    def to_dict(self):
        return {"schema_version": self.schema_version, "mode_ids": list(self.mode_ids)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "mode_ids"}:
            raise ValueError("Pawprint mode fields are closed")
        return cls(value["mode_ids"], value["schema_version"])

    def validate_schema(self, schema):
        if (not isinstance(schema, Mapping) or set(schema) != {"mode_count", "min_modes", "max_modes", "modes"}
                or any(type(schema[key]) is not int for key in ("mode_count", "min_modes", "max_modes"))
                or (schema["mode_count"], schema["min_modes"], schema["max_modes"]) != (1, 1, 3)
                or not isinstance(schema["modes"], Mapping) or tuple(schema["modes"]) != self.mode_ids):
            raise ValueError("Pawprint pricing requires its complete three-mode definition")

        for mode, definition in schema["modes"].items():
            if not isinstance(definition, Mapping) or set(definition) != {"groups", "effects", "mechanics"}:
                raise ValueError("Each Pawprint mode needs a closed standalone target plan")
            groups = definition["groups"]
            if (not isinstance(groups, (list, tuple)) or len(groups) > 1
                    or groups and (not isinstance(groups[0], Mapping)
                                   or groups[0].get("id") != f"{mode}_target_1"
                                   or type(groups[0].get("count")) is not int
                                   or groups[0]["count"] != 1 or groups[0].get("up_to"))):
                raise ValueError("Pawprint modes support a single required slot or no target")
            if _has_persistent_binding(definition["effects"]):
                raise ValueError("Pawprint modes require independent result bindings")

    def available_choices(self):
        return tuple(
            tuple(mode for mode, count in zip(self.mode_ids, counts, strict=True) for _ in range(count))
            for counts in product(range(6), range(3), range(2))
            if sum(weight * count for weight, count in enumerate(counts, 1)) <= 5
        )

    def selection(self, submitted):
        if not isinstance(submitted, (list, tuple)) or len(submitted) > 5:
            raise ValueError("A Pawprint choice needs at most five mode selections")
        if any(type(mode) is not str for mode in submitted):
            raise ValueError("A Pawprint selection has an invalid mode")
        printed = tuple(mode.partition("_repeat_")[0] for mode in submitted)
        if not set(printed) <= set(self.mode_ids):
            raise ValueError("A Pawprint selection has an unknown mode")
        counts = tuple(printed.count(mode) for mode in self.mode_ids)
        if sum(weight * count for weight, count in enumerate(counts, 1)) > 5:
            raise ValueError("The selected modes exceed five pawprints")
        instances = tuple(
            (mode if index == 1 else f"{mode}_repeat_{index}", mode)
            for mode, count in zip(self.mode_ids, counts, strict=True)
            for index in range(1, count + 1)
        )
        if tuple(submitted) != printed and sorted(submitted) != sorted(alias for alias, _ in instances):
            raise ValueError("Quoted Pawprint mode instances must remain distinct and complete")
        return instances

    def selected_schema(self, schema, instances):
        self.validate_schema(schema)
        modes = {}
        for alias, mode in instances:
            definition = deepcopy(dict(schema["modes"][mode]))
            if definition["groups"] and alias != mode:
                definition["groups"][0]["id"] = f"{alias}_target_1"
            modes[alias] = definition
        count = len(instances)
        # Keep a modal schema for the genuine zero-mode cast, with no selected groups.
        return {
            "mode_count": count, "min_modes": count, "max_modes": count, "modes": modes,
            PAWPRINT_SELECTION_FIELD: True,
        }


def _has_persistent_binding(value):
    if isinstance(value, Mapping):
        return (value.get("binding_id") is not None or value.get("kind") == "target_owner_information"
                or any(_has_persistent_binding(child) for child in value.values()))
    if isinstance(value, (list, tuple)):
        return any(_has_persistent_binding(child) for child in value)
    return False


def rebase_pawprint_controller_reference(value, offset, count):
    """The printed recipient is local to one selected modal occurrence."""
    if isinstance(value, Mapping):
        if value.get("kind") == TARGET_CONTROLLER_INFORMATION_KIND:
            index = controller_reference_index(value)
            if index >= count:
                raise ValueError("A Pawprint result refers outside its own modal target slots")
            return target_controller_reference(offset + index)
        return {key: rebase_pawprint_controller_reference(child, offset, count)
                for key, child in value.items()}
    if isinstance(value, list):
        return [rebase_pawprint_controller_reference(child, offset, count) for child in value]
    if isinstance(value, tuple):
        return tuple(rebase_pawprint_controller_reference(child, offset, count) for child in value)
    return value
