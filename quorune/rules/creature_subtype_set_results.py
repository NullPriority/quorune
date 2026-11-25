from __future__ import annotations

"""Closed multi-layer results for adding or removing all creature subtypes."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_model import ContinuousOperation, Layer
from ..replacement.immutable import thaw_value
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION


CREATURE_SUBTYPE_SET_RESULT_VERSION = 25
CREATURE_SUBTYPE_SET_RESULT_MECHANIC = "temporary-creature-subtype-set-result"
CREATURE_SUBTYPE_SET_RESULT_CAPABILITY = "continuous.resolution.creature_subtype_set_result"


@dataclass(frozen=True, slots=True)
class CreatureSubtypeSetResult:
    scope: str
    subtype_set: str
    modifier: tuple[int, int] | None = None
    base: tuple[int, int] | None = None
    flying: bool = False

    def __post_init__(self):
        if (
            type(self.scope) is not str
            or self.scope not in {"one_creature", "up_to_two_creatures", "target_players_creatures"}
            or type(self.subtype_set) is not str or self.subtype_set not in {"all", "none"}
            or type(self.flying) is not bool
        ):
            raise ValueError("Unknown creature subtype set result")
        for name in ("modifier", "base"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, (list, tuple)) or len(value) != 2 or any(type(number) is not int for number in value):
                raise ValueError("Creature subtype set characteristics require an integer pair")
            object.__setattr__(self, name, tuple(value))
        if self.scope != "one_creature" and (
            self.subtype_set != "all" or self.modifier is None or self.base is not None or self.flying
        ):
            raise ValueError("A set result requires its fixed positive subtype and modifier composition")
        if (self.base is not None) != self.flying or self.base is not None and (
            self.subtype_set != "all" or self.modifier is not None
        ):
            raise ValueError("A base-setting result retains the complete flying and subtype sequence")
        if self.scope == "one_creature" and self.modifier is not None and self.subtype_set != "none":
            raise ValueError("A one-creature modifier requires its full subtype removal result")
        if self.modifier == (0, 0):
            raise ValueError("A represented modifier must change either power or toughness")

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"scope", "subtype_set", "modifier", "base", "flying"}:
            raise ValueError("Creature subtype set effect has unknown fields")
        return cls(**dict(value))

    def to_dict(self):
        return {
            "scope": self.scope, "subtype_set": self.subtype_set,
            "modifier": list(self.modifier) if self.modifier is not None else None,
            "base": list(self.base) if self.base is not None else None, "flying": self.flying,
        }

    def to_instruction(self):
        return {
            "op": CHARACTERISTIC_OPERATION, "schema_version": CREATURE_SUBTYPE_SET_RESULT_VERSION,
            "subtype_set_result": self.to_dict(),
            "subjects": "$target.0" if self.scope == "target_players_creatures" else "$targets",
        }

    def to_target_schema(self):
        if self.scope == "target_players_creatures":
            return {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
        schema = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"]}
        if self.scope == "one_creature":
            return {**schema, "count": 1}
        return {**schema, "min": 0, "max": 2}

    def components(self):
        from ..continuous_effect_state import ResolutionContinuousComponent

        result = [ResolutionContinuousComponent(Layer.TYPE, "4", (
            ContinuousOperation("set_creature_subtype_set", self.subtype_set),
        ))]
        if self.flying:
            result.append(ResolutionContinuousComponent(Layer.ABILITY, "6", (ContinuousOperation("add_ability", "Flying"),)))
        if self.base is not None:
            result.append(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, "7b", (
                ContinuousOperation("set_power_toughness", self.base),
            )))
        if self.modifier is not None:
            result.append(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, "7c", (
                ContinuousOperation("modify_power_toughness", self.modifier),
            )))
        return tuple(result)


def is_creature_subtype_set_result(value):
    return isinstance(value, Mapping) and value.get("op") == CHARACTERISTIC_OPERATION and (
        value.get("schema_version") == CREATURE_SUBTYPE_SET_RESULT_VERSION or "subtype_set_result" in value
    )


def creature_subtype_set_instruction_dependencies(effects, target_schema):
    effect, = effects
    if not is_creature_subtype_set_result(effect) or type(effect.get("schema_version")) is not int:
        raise ValueError("Unknown creature subtype result version")
    spec = CreatureSubtypeSetResult.from_mapping(effect.get("subtype_set_result"))
    expected = spec.to_target_schema()
    if (
        not isinstance(target_schema, Mapping) or thaw_value(effect) != spec.to_instruction()
        or thaw_value(target_schema) != expected
        or any(type(target_schema[key]) is not int for key in ("count", "min", "max") if key in expected)
    ):
        raise ValueError("Creature subtype set requires its complete named scope and target")
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES

    return (
        CREATURE_SUBTYPE_SET_RESULT_CAPABILITY, "target.revalidate_resolution",
        *(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Flying"] if spec.flying else ()),
    )


def creature_subtype_set_node_capabilities(*, effects, target_schema, mechanics):
    if not {CREATURE_SUBTYPE_SET_RESULT_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"} <= set(mechanics):
        return ()
    try:
        return creature_subtype_set_instruction_dependencies(effects, target_schema)
    except (ValueError, TypeError):
        return ()


def creature_subtype_set_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(is_creature_subtype_set_result(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = creature_subtype_set_instruction_dependencies(program.effects, program.target_schema)
    spec = CreatureSubtypeSetResult.from_mapping(program.effects[0]["subtype_set_result"])
    if (program.event, program.active_zone) == ("resolve", "stack"):
        return required
    if (program.event, program.active_zone) == ("activate", "battlefield") and (
        spec.scope == "one_creature" and spec.modifier is None and spec.base is None
    ):
        return required
    raise ValueError("The subtype-set result requires its exact activation or resolving spell")
