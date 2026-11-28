from __future__ import annotations

"""Fixed contributions triggered by a permanent's own tap mana ability."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations_with_replacement
import json
from typing import Any

from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .chosen_color_mana_model import ChosenColorManaOutput
from .fixed_mana_abilities import FixedManaMode
from .util import stable_json


LAND_MANA_TAP_EVENT = "land.tapped_for_mana"
LAND_MANA_TAP_VARIANT = "ordinary_land_mana_tap"


def land_mana_tap_condition(*, nonbasic):
    return {"field": "land_basic", "op": "eq", "value": False} if nonbasic else None


def land_mana_tap_damage_effect():
    return {"op": "damage", "source": "$source", "target": "$context.player", "amount": 1}


SOURCE_MANA_TAP_EVENT = "permanent.tapped_for_mana"
SOURCE_MANA_TAP_VARIANT = "ordinary_source_mana_tap"
SOURCE_MANA_TAP_CAPABILITY = "trigger.event.source_tap_for_mana"
SOURCE_MANA_TAP_DAMAGE_MECHANIC = "source-mana-tap-damage"
SOURCE_MANA_TAP_TOKEN_VARIANT = "source_mana_tap_opponent_creates_token"
SOURCE_MANA_TAP_TOKEN_CAPABILITY = "trigger.event.source_tap_for_mana_opponent_token"
SOURCE_MANA_TAP_TARGET_TOKEN_CAPABILITY = "trigger.event.source_tap_for_mana_target_token"
SOURCE_MANA_TAP_TARGET_TOKEN_TEMPLATE = "fixed-source-mana-tap-target-spirit-v1"


def source_mana_tap_condition():
    return {
        "all": [
            {"field": "card_object_identity", "op": "eq", "value": "$source.logical_object_id"},
            {"field": "player", "op": "eq", "value": "$source.controller"},
        ]
    }


def source_mana_tap_observer_mismatch(source, program, event, condition, context):
    """Recognize a proven false occurrence for the closed live source-tap trigger."""
    if (event != SOURCE_MANA_TAP_EVENT or context is None
            or getattr(program, "event", None) != event
            or getattr(program, "active_zone", None) != "battlefield"
            or getattr(source, "zone", None) != "battlefield"
            or getattr(program, "trust_level", None) != "trusted"
            or SOURCE_MANA_TAP_DAMAGE_MECHANIC not in getattr(program, "coverage", ())
            or SOURCE_MANA_TAP_CAPABILITY not in getattr(program, "capability_dependencies", ())
            or "intervening_condition_controller" in context):
        return False
    from .replacement.immutable import thaw_value
    from .trigger_participation_query_model import trigger_prohibition_can_observe

    if trigger_prohibition_can_observe(event) or thaw_value(condition) != source_mana_tap_condition():
        return False
    subject = context.get("card_object_identity")
    actor = context.get("player")
    identity = getattr(source, "logical_object_id", None)
    controller = getattr(source, "controller", None)
    if any(type(value) is not str or not value for value in (subject, actor, identity, controller)):
        return False
    return (subject, actor) != (identity, controller)


def source_mana_tap_damage_effect():
    return {"op": "damage_each_opponent", "source": "$source", "amount": 1}


def source_mana_tap_spirit_effect():
    from .fixed_token_production import TOKEN_LITERAL_NAME_VERSION_FIELD

    return {
        "op": "create_token", "name": "Spirit Token", "quantity": 1, "target": "$target.0",
        "characteristics": {
            "type_line": "Token Creature — Spirit", TOKEN_LITERAL_NAME_VERSION_FIELD: 1,
            "colors": [], "power": "1", "toughness": "1",
        },
    }


FIXED_TRIGGERED_MANA_EVENT = "mana.tap.production"
FIXED_TRIGGERED_MANA_FIELD = "fixed_triggered_mana"
FIXED_TRIGGERED_MANA_CAPABILITY = "mana.triggered.fixed_output"
CHOSEN_TRIGGERED_MANA_CAPABILITY = "mana.triggered.chosen_output"
SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY = "mana.triggered.source_chosen_color"
SOURCE_CHOSEN_TRIGGERED_MANA_TEMPLATE = "source-chosen-color-triggered-mana-v3"
TRIGGERED_MANA_OPTION_FIELD = "triggered_mana_option"
MANA_LAND_SUBTYPES = frozenset({"plains", "island", "swamp", "mountain", "forest"})


def _compact(bundle: Mapping[str, int]) -> dict[str, int]:
    return {color: count for color, count in bundle.items() if count}


@dataclass(frozen=True, slots=True)
class TriggeredManaChoice:
    kind: str
    amount: int

    def __post_init__(self) -> None:
        if type(self.kind) is not str or type(self.amount) is not int or (
            (self.kind, self.amount) not in {("produced_type", 1), ("any_color", 1), ("any_color", 2)}
        ):
            raise ValueError("Unsupported triggered mana output choice")

    def to_dict(self) -> dict[str, str | int]:
        return {"kind": self.kind, "amount": self.amount}

    @classmethod
    def from_dict(cls, value: Any) -> TriggeredManaChoice:
        if not isinstance(value, Mapping) or set(value) != {"kind", "amount"}:
            raise ValueError("Malformed triggered mana output choice")
        return cls(value["kind"], value["amount"])

    def options(self, base: Mapping[str, int]) -> tuple[FixedManaMode, ...]:
        if self.kind == "produced_type":
            return tuple(FixedManaMode.from_bundle({color: 1}) for color in "WUBRGC" if base.get(color, 0) > 0)
        return tuple(FixedManaMode.from_bundle(Counter(colors))
                     for colors in combinations_with_replacement("WUBRG", self.amount))


@dataclass(frozen=True, slots=True)
class TriggeredManaOption:
    base: FixedManaMode
    choices: tuple[FixedManaMode, ...]

    def encode(self) -> str:
        return stable_json([_compact(self.base.bundle), [_compact(choice.bundle) for choice in self.choices]])

    @classmethod
    def decode(cls, value: Any) -> TriggeredManaOption:
        if type(value) is not str or len(value) > 65536:
            raise ValueError("A triggered mana option requires a bounded encoded selection")
        try:
            raw = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise ValueError("Malformed triggered mana option") from exc
        if (type(raw) is not list or len(raw) != 2 or type(raw[0]) is not dict
                or type(raw[1]) is not list or any(type(item) is not dict for item in raw[1])):
            raise ValueError("Malformed triggered mana option")
        result = cls(FixedManaMode.from_bundle(raw[0]), tuple(FixedManaMode.from_bundle(item) for item in raw[1]))
        if result.encode() != value:
            raise ValueError("Triggered mana option requires canonical mana bundles")
        return result


@dataclass(frozen=True, slots=True)
class FixedTriggeredManaSpec:
    scope: str
    subject: str
    output: FixedManaMode | TriggeredManaChoice | ChosenColorManaOutput
    produced_color: str | None = None
    basic_only: bool = False
    subtypes_any: tuple[str, ...] = ()
    requires_monarch: bool = False

    def __post_init__(self) -> None:
        if (type(self.scope) is not str or self.scope not in {"attached", "controller", "any"}
                or type(self.subject) is not str
                or self.subject not in {"permanent", "land", "creature", *MANA_LAND_SUBTYPES}
                or not isinstance(self.output, (FixedManaMode, TriggeredManaChoice, ChosenColorManaOutput))):
            raise ValueError("Unsupported fixed triggered mana subject or output")
        if self.produced_color is not None and (
            type(self.produced_color) is not str or self.produced_color not in "WUBRGC" or len(self.produced_color) != 1
        ):
            raise ValueError("Unknown triggering mana type")
        if (type(self.basic_only) is not bool or type(self.requires_monarch) is not bool
                or type(self.subtypes_any) is not tuple
                or any(type(subtype) is not str or subtype not in MANA_LAND_SUBTYPES for subtype in self.subtypes_any)
                or tuple(sorted(set(self.subtypes_any))) != self.subtypes_any):
            raise ValueError("Malformed triggered mana qualifications")
        if isinstance(self.output, ChosenColorManaOutput):
            self._validate_source_chosen_output()
            return
        if self.scope == "attached" and (self.subject != "land" or self.produced_color is not None):
            raise ValueError("An attached mana trigger requires a land and unrestricted production type")
        if isinstance(self.output, FixedManaMode) and (self.basic_only or self.subtypes_any or self.requires_monarch):
            raise ValueError("Legacy fixed triggered mana uses its original subject shape")
        if isinstance(self.output, TriggeredManaChoice) and (self.subject != "land" or self.produced_color is not None):
            raise ValueError("Chosen triggered mana requires an unfiltered land production")
        if self.requires_monarch and self.scope != "controller":
            raise ValueError("The monarch condition belongs to the producing controller")

    def _validate_source_chosen_output(self) -> None:
        if ((self.scope, self.subject, self.basic_only) not in {("attached", "forest", False), ("any", "land", True)}
                or self.produced_color is not None or self.subtypes_any or self.requires_monarch
                or self.output.amount != 1 or self.output.fixed_alternative is not None
                or self.output.restriction is not None):
            raise ValueError("Source-chosen triggered mana requires its linked color and exact tap relation")

    @property
    def capability(self) -> str:
        if isinstance(self.output, ChosenColorManaOutput):
            return SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY
        return CHOSEN_TRIGGERED_MANA_CAPABILITY if isinstance(self.output, TriggeredManaChoice) else FIXED_TRIGGERED_MANA_CAPABILITY

    def to_dict(self) -> dict[str, Any]:
        value = {
            "scope": self.scope,
            "subject": self.subject,
            "produced_color": self.produced_color,
        }
        if isinstance(self.output, FixedManaMode):
            return {**value, "schema_version": 1, "output": _compact(self.output.bundle)}
        if isinstance(self.output, ChosenColorManaOutput):
            return {**value, "schema_version": 3, "output": self.output.to_dict(), "basic_only": self.basic_only}
        return {**value, "schema_version": 2, "output": self.output.to_dict(), "basic_only": self.basic_only,
                "subtypes_any": list(self.subtypes_any), "requires_monarch": self.requires_monarch}

    @classmethod
    def from_dict(cls, value: Any) -> FixedTriggeredManaSpec:
        version = value.get("schema_version") if isinstance(value, Mapping) else None
        base = {"schema_version", "scope", "subject", "output", "produced_color"}
        if type(version) is not int or version not in (1, 2, 3):
            raise ValueError("Malformed fixed triggered mana descriptor")
        if version == 1 and set(value) == base and isinstance(value["output"], Mapping):
            result = cls(value["scope"], value["subject"], FixedManaMode.from_bundle(value["output"]), value["produced_color"])
        elif (version == 2 and set(value) == base | {"basic_only", "subtypes_any", "requires_monarch"}
              and type(value["subtypes_any"]) is list):
            result = cls(value["scope"], value["subject"], TriggeredManaChoice.from_dict(value["output"]),
                         value["produced_color"], value["basic_only"], tuple(value["subtypes_any"]), value["requires_monarch"])
        elif version == 3 and set(value) == base | {"basic_only"}:
            result = cls(value["scope"], value["subject"], ChosenColorManaOutput.from_dict(value["output"]),
                         value["produced_color"], value["basic_only"])
        else:
            raise ValueError("Malformed triggered mana descriptor fields")
        if result.to_dict() != value:
            raise ValueError("Fixed triggered mana requires a canonical output")
        return result


def fixed_triggered_mana_spec(program: Any) -> FixedTriggeredManaSpec | None:
    condition = program.event_condition or {}
    if (program.event != FIXED_TRIGGERED_MANA_EVENT and FIXED_TRIGGERED_MANA_FIELD not in condition
            and not {FIXED_TRIGGERED_MANA_CAPABILITY, CHOSEN_TRIGGERED_MANA_CAPABILITY,
                     SOURCE_CHOSEN_TRIGGERED_MANA_CAPABILITY}.intersection(program.capability_dependencies)):
        return None
    if (program.event != FIXED_TRIGGERED_MANA_EVENT or program.active_zone != "battlefield"
            or set(condition) != {FIXED_TRIGGERED_MANA_FIELD}
            or program.target_schema is not None or program.cost_schema is not None
            or program.trigger_limit is not None or program.destination is not None
            or program.effects or program.handlers or program.requires_arbiter
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
        raise ValueError("Fixed triggered mana requires one current battlefield ability")
    return FixedTriggeredManaSpec.from_dict(condition[FIXED_TRIGGERED_MANA_FIELD])


def fixed_triggered_mana_runtime_dependencies(program: Any) -> tuple[str, ...]:
    spec = fixed_triggered_mana_spec(program)
    return (spec.capability,) if spec is not None else ()
