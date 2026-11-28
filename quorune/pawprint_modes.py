"""The five-pawprint budget and a separate slot for each chosen mode instance."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations_with_replacement, product
import re

from .target_controller_reference import (
    TARGET_CONTROLLER_INFORMATION_KIND, controller_reference_index, target_controller_reference,
)


PAWPRINT_FIELD = "pawprint_modes"
PAWPRINT_CAPABILITY = "casting.modal.pawprint_budget"
PAWPRINT_MECHANIC = "fixed-pawprint-mode-budget"
PAWPRINT_RESULT_MODES = "pawprint_selected_modes"
PAWPRINT_SELECTION_FIELD = "pawprint_instances"

REPEATABLE_MODE_FIELD = "repeatable_mode_choices"
REPEATABLE_MODE_CAPABILITY = "casting.modal.fixed_repeatable_choices"
REPEATABLE_MODE_MECHANIC = "fixed-repeatable-mode-choices"
REPEATABLE_MODE_RESULT = "repeatable_selected_modes"
REPEATABLE_MODE_SELECTION_FIELD = "repeatable_mode_instances"


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
        _validate_repeatable_mode_schema(schema, self.mode_ids)

    def available_choices(self):
        return tuple(
            tuple(mode for mode, count in zip(self.mode_ids, counts, strict=True) for _ in range(count))
            for counts in product(range(6), range(3), range(2))
            if sum(weight * count for weight, count in enumerate(counts, 1)) <= 5
        )

    def selection(self, submitted):
        instances = _repeat_mode_instances(submitted, self.mode_ids, max_modes=5)
        weights = {mode: index for index, mode in enumerate(self.mode_ids, 1)}
        if sum(weights[mode] for _, mode in instances) > 5:
            raise ValueError("The selected modes exceed five pawprints")
        return instances

    def selected_schema(self, schema, instances):
        self.validate_schema(schema)
        return _selected_mode_schema(schema, instances, marker=PAWPRINT_SELECTION_FIELD)


@dataclass(frozen=True, slots=True)
class FixedRepeatableModeChoices:
    mode_ids: tuple[str, ...] = ("mode_1", "mode_2", "mode_3")
    count: int = 3
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.count) is not int or self.count != 3
                or not isinstance(self.mode_ids, (list, tuple))
                or tuple(self.mode_ids) != ("mode_1", "mode_2", "mode_3")):
            raise ValueError("The exact repeatable choice requires three selections from three printed modes")
        object.__setattr__(self, "mode_ids", tuple(self.mode_ids))

    def to_dict(self):
        return {"schema_version": self.schema_version, "mode_ids": list(self.mode_ids), "count": self.count}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "mode_ids", "count"}:
            raise ValueError("Fixed repeatable mode fields are closed")
        return cls(value["mode_ids"], value["count"], value["schema_version"])

    def validate_schema(self, schema):
        _validate_repeatable_mode_schema(schema, self.mode_ids)

    def available_choices(self):
        return tuple(combinations_with_replacement(self.mode_ids, self.count))

    def selection(self, submitted):
        instances = _repeat_mode_instances(submitted, self.mode_ids, max_modes=self.count)
        if len(instances) != self.count:
            raise ValueError("Choose exactly three mode occurrences")
        return instances

    def selected_schema(self, schema, instances):
        self.validate_schema(schema)
        return _selected_mode_schema(schema, instances, marker=REPEATABLE_MODE_SELECTION_FIELD)


def has_repeatable_mode_slots(schema):
    return schema.get(PAWPRINT_SELECTION_FIELD) is True or schema.get(REPEATABLE_MODE_SELECTION_FIELD) is True


def _repeat_mode_instances(submitted, mode_ids, *, max_modes):
    if not isinstance(submitted, (list, tuple)) or len(submitted) > max_modes:
        raise ValueError("Too many repeatable mode selections")
    if any(type(mode) is not str for mode in submitted):
        raise ValueError("An invalid mode was selected")
    printed = tuple(mode.partition("_repeat_")[0] for mode in submitted)
    if not set(printed) <= set(mode_ids):
        raise ValueError("An unknown mode was selected")
    instances = tuple(
        (mode if index == 1 else f"{mode}_repeat_{index}", mode)
        for mode in mode_ids for index in range(1, printed.count(mode) + 1)
    )
    if tuple(submitted) != printed and sorted(submitted) != sorted(alias for alias, _ in instances):
        raise ValueError("Quoted mode instances must remain distinct and complete")
    return instances


def _selected_mode_schema(schema, instances, *, marker):
    modes = {}
    for alias, mode in instances:
        definition = deepcopy(dict(schema["modes"][mode]))
        if definition["groups"] and alias != mode:
            definition["groups"][0]["id"] = f"{alias}_target_1"
        modes[alias] = definition
    count = len(instances)
    return {"mode_count": count, "min_modes": count, "max_modes": count, "modes": modes, marker: True}


def _validate_repeatable_mode_schema(schema, mode_ids):
    if (not isinstance(schema, Mapping) or set(schema) != {"mode_count", "min_modes", "max_modes", "modes"}
            or any(type(schema[key]) is not int for key in ("mode_count", "min_modes", "max_modes"))
            or (schema["mode_count"], schema["min_modes"], schema["max_modes"]) != (1, 1, 3)
            or not isinstance(schema["modes"], Mapping) or tuple(schema["modes"]) != mode_ids):
        raise ValueError("Repeatable mode selection requires its complete three-mode definition")

    _validate_mode_branches(schema)



def _validate_mode_branches(schema):
    for mode, definition in schema["modes"].items():
        if not isinstance(definition, Mapping) or set(definition) != {"groups", "effects", "mechanics"}:
            raise ValueError("Each repeatable mode needs a closed standalone target plan")
        groups = definition["groups"]
        if (not isinstance(groups, (list, tuple)) or len(groups) > 1
                or groups and (not isinstance(groups[0], Mapping)
                               or groups[0].get("id") != f"{mode}_target_1"
                               or type(groups[0].get("count")) is not int
                               or groups[0]["count"] != 1 or groups[0].get("up_to"))):
            raise ValueError("Repeatable modes support a single required slot or no target")
        if _has_persistent_binding(definition["effects"]):
            raise ValueError("Repeatable modes require independent result bindings")

def _has_persistent_binding(value):
    if isinstance(value, Mapping):
        return (value.get("binding_id") is not None or value.get("kind") == "target_owner_information"
                or any(_has_persistent_binding(child) for child in value.values()))
    if isinstance(value, (list, tuple)):
        return any(_has_persistent_binding(child) for child in value)
    return False


def rebase_pawprint_controller_reference(value, offset, count, *, live_controller=False):
    """The printed recipient is local to one selected modal occurrence."""
    if live_controller and isinstance(value, str):
        match = re.fullmatch(r"\$target\.current_controller[.\[](?P<index>\d+)\]?", value)
        if match is not None:
            index = int(match["index"])
            if index >= count:
                raise ValueError("A repeatable mode payer must use its own target slot")
            return f"$target.current_controller.{offset + index}"
    if isinstance(value, Mapping):
        if value.get("kind") == TARGET_CONTROLLER_INFORMATION_KIND:
            index = controller_reference_index(value)
            if index >= count:
                raise ValueError("A Pawprint result refers outside its own modal target slots")
            return target_controller_reference(offset + index)
        return {key: rebase_pawprint_controller_reference(child, offset, count, live_controller=live_controller)
                for key, child in value.items()}
    if isinstance(value, list):
        return [rebase_pawprint_controller_reference(child, offset, count, live_controller=live_controller) for child in value]
    if isinstance(value, tuple):
        return tuple(rebase_pawprint_controller_reference(child, offset, count, live_controller=live_controller) for child in value)
    return value
