from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectOrigin,
    ContinuousEffectRelation,
    ContinuousOperation,
    Layer,
)
from ..object_predicate import ObjectQuerySpec
from ..ability_fragments import ability_fragment_from_dict
from ..characteristic_fragments import CharacteristicQuantitySpec
from .component_registry import exact_fields
from .context import SemanticNodeError
from .continuous_components import ContinuousEffectSourceContext


ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID = (
    "continuous.attached.fixed-characteristics.v1"
)
_TYPE_OPERATIONS = frozenset({"set_types", "add_types", "remove_types"})
_TYPE_FIELDS = frozenset({"supertypes", "card_types", "subtypes"})
_COLOR_OPERATIONS = frozenset(
    {"set_colors", "add_colors", "remove_colors", "remove_all_colors"}
)


@dataclass(frozen=True, slots=True)
class AttachedFixedCharacteristicsNode:
    subject_types_all: tuple[str, ...]
    type_operations: tuple[ContinuousOperation, ...]
    color_operations: tuple[ContinuousOperation, ...]
    add_abilities: tuple[str, ...]
    remove_abilities: tuple[str, ...]
    remove_all_abilities: bool
    add_rules_text: tuple[str, ...]
    add_ability_fragments: tuple[Mapping[str, Any], ...]
    power: int
    toughness: int
    base_power: int | None
    base_toughness: int | None
    quantity: CharacteristicQuantitySpec | None
    quantity_power: int
    quantity_toughness: int


def _ability_values(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        type(item) is not str or not item.strip() for item in value
    ):
        raise SemanticNodeError(f"{field} must be a string array")
    result = tuple(item.strip().title() for item in value)
    if len({item.casefold() for item in result}) != len(result):
        raise SemanticNodeError(f"{field} values must be unique")
    return result


def _rules_text_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        type(item) is not str or not item.strip() for item in value
    ):
        raise SemanticNodeError(
            "modifier.add_rules_text must be a string array"
        )
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise SemanticNodeError(
            "modifier.add_rules_text values must be unique"
        )
    return result


def _ability_fragment_values(
    value: Any,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise SemanticNodeError(
            "modifier.add_ability_fragments must be an array"
        )
    result: list[Mapping[str, Any]] = []
    for index, candidate in enumerate(value):
        if not isinstance(candidate, Mapping):
            raise SemanticNodeError(
                "modifier.add_ability_fragments entries must be objects"
            )
        try:
            ability_fragment_from_dict(candidate)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        result.append(dict(candidate))
    return tuple(result)


def _type_operations(value: Any) -> tuple[ContinuousOperation, ...]:
    if not isinstance(value, list):
        raise SemanticNodeError("modifier.type_operations must be an array")
    result: list[ContinuousOperation] = []
    for index, candidate in enumerate(value):
        if not isinstance(candidate, Mapping):
            raise SemanticNodeError(
                f"modifier.type_operations[{index}] must be an object"
            )
        exact_fields(
            candidate,
            {"op", "field", "values"},
            field=f"modifier.type_operations[{index}]",
        )
        if candidate["op"] not in _TYPE_OPERATIONS:
            raise SemanticNodeError("Unsupported attached type operation")
        if candidate["field"] not in _TYPE_FIELDS:
            raise SemanticNodeError("Unsupported attached type field")
        try:
            result.append(
                ContinuousOperation(
                    op=str(candidate["op"]),
                    field=str(candidate["field"]),
                    value=candidate["values"],
                )
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    return tuple(result)


def _color_operations(value: Any) -> tuple[ContinuousOperation, ...]:
    if not isinstance(value, list):
        raise SemanticNodeError("modifier.color_operations must be an array")
    result: list[ContinuousOperation] = []
    for index, candidate in enumerate(value):
        if not isinstance(candidate, Mapping):
            raise SemanticNodeError(
                f"modifier.color_operations[{index}] must be an object"
            )
        operation = candidate.get("op")
        expected = {"op"} if operation == "remove_all_colors" else {
            "op",
            "values",
        }
        exact_fields(
            candidate,
            expected,
            field=f"modifier.color_operations[{index}]",
        )
        if operation not in _COLOR_OPERATIONS:
            raise SemanticNodeError("Unsupported attached color operation")
        try:
            result.append(
                ContinuousOperation(
                    op=str(operation),
                    value=(
                        None
                        if operation == "remove_all_colors"
                        else candidate["values"]
                    ),
                )
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    return tuple(result)


_LEGACY_MODIFIER_FIELDS = {
    "type_operations",
    "add_abilities",
    "remove_abilities",
    "add_rules_text",
    "add_ability_fragments",
    "power",
    "toughness",
}
_EXTENDED_MODIFIER_FIELDS = {
    *_LEGACY_MODIFIER_FIELDS,
    "color_operations",
    "remove_all_abilities",
    "base_power",
    "base_toughness",
    "quantity",
    "quantity_power",
    "quantity_toughness",
}


def _subject_types(condition: Any) -> tuple[str, ...]:
    if not isinstance(condition, Mapping):
        raise SemanticNodeError("runtime handler condition must be an object")
    if set(condition) not in ({"relation"}, {"relation", "types_all"}):
        raise SemanticNodeError(
            "runtime handler condition has missing or unknown fields"
        )
    if condition["relation"] != "source_attached_object":
        raise SemanticNodeError(
            "attached characteristics require source_attached_object"
        )
    raw = condition.get("types_all", [])
    if not isinstance(raw, list) or any(
        type(value) is not str
        or value.casefold() not in {"creature", "land"}
        for value in raw
    ):
        raise SemanticNodeError(
            "attached characteristic subject types must be creature or land"
        )
    result = tuple(sorted(value.casefold() for value in raw))
    if len(result) != len(set(result)):
        raise SemanticNodeError(
            "attached characteristic subject types must be unique"
        )
    return result


def _quantity_values(
    modifier: Mapping[str, Any],
) -> tuple[CharacteristicQuantitySpec | None, int, int]:
    raw_quantity = modifier.get("quantity")
    try:
        quantity = (
            CharacteristicQuantitySpec.from_dict(raw_quantity)
            if isinstance(raw_quantity, Mapping)
            else None
        )
    except ValueError as exc:
        raise SemanticNodeError(str(exc)) from exc
    if raw_quantity is not None and quantity is None:
        raise SemanticNodeError(
            "attached quantity must be a typed characteristic quantity"
        )
    power = modifier.get("quantity_power", 0)
    toughness = modifier.get("quantity_toughness", 0)
    if type(power) is not int or type(toughness) is not int:
        raise SemanticNodeError(
            "attached quantity multipliers must be integers"
        )
    if (quantity is None) != (power == 0 and toughness == 0):
        raise SemanticNodeError(
            "attached dynamic modifiers require one nonzero typed quantity"
        )
    return quantity, power, toughness


def _attached_characteristics_node(
    subject_types_all: tuple[str, ...],
    modifier: Any,
) -> AttachedFixedCharacteristicsNode:
    if not isinstance(modifier, Mapping):
        raise SemanticNodeError("runtime handler modifier must be an object")
    if set(modifier) not in (
        _LEGACY_MODIFIER_FIELDS,
        _EXTENDED_MODIFIER_FIELDS,
    ):
        raise SemanticNodeError(
            "runtime handler modifier has missing or unknown fields"
        )
    power = modifier["power"]
    toughness = modifier["toughness"]
    if type(power) is not int or type(toughness) is not int:
        raise SemanticNodeError(
            "attached power/toughness modifiers must be integers"
        )
    base_power = modifier.get("base_power")
    base_toughness = modifier.get("base_toughness")
    if (base_power is None) != (base_toughness is None) or any(
        value is not None and type(value) is not int
        for value in (base_power, base_toughness)
    ):
        raise SemanticNodeError(
            "attached base power/toughness must be one integer pair"
        )
    quantity, quantity_power, quantity_toughness = _quantity_values(modifier)
    remove_all = modifier.get("remove_all_abilities", False)
    if type(remove_all) is not bool:
        raise SemanticNodeError(
            "modifier.remove_all_abilities must be boolean"
        )
    return AttachedFixedCharacteristicsNode(
        subject_types_all=subject_types_all,
        type_operations=_type_operations(modifier["type_operations"]),
        color_operations=_color_operations(modifier.get("color_operations", [])),
        add_abilities=_ability_values(
            modifier["add_abilities"], field="modifier.add_abilities"
        ),
        remove_abilities=_ability_values(
            modifier["remove_abilities"], field="modifier.remove_abilities"
        ),
        remove_all_abilities=remove_all,
        add_rules_text=_rules_text_values(modifier["add_rules_text"]),
        add_ability_fragments=_ability_fragment_values(
            modifier["add_ability_fragments"]
        ),
        power=power,
        toughness=toughness,
        base_power=base_power,
        base_toughness=base_toughness,
        quantity=quantity,
        quantity_power=quantity_power,
        quantity_toughness=quantity_toughness,
    )


def _has_characteristic_modifier(
    node: AttachedFixedCharacteristicsNode,
) -> bool:
    return bool(
        node.type_operations
        or node.color_operations
        or node.add_abilities
        or node.remove_abilities
        or node.remove_all_abilities
        or node.add_rules_text
        or node.add_ability_fragments
        or node.power
        or node.toughness
        or node.base_power is not None
        or node.quantity is not None
    )


@dataclass(frozen=True, slots=True)
class AttachedFixedCharacteristicsHandler:
    """CR 611.3 live characteristic effect over the attached object."""

    handler_id: str = ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.attached.fixed_characteristics"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "301.5",
        "303.4",
        "604.1",
        "611.3a",
        "611.3b",
        "613.1d",
        "613.1f",
        "613.1g",
        "613.6",
        "701.3c",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.attached.fixed_characteristics",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> AttachedFixedCharacteristicsNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "condition",
                "modifier",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Runtime handler ID does not match registry")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(f"{self.handler_id} must handle {self.event}")
        node = _attached_characteristics_node(
            _subject_types(descriptor["condition"]),
            descriptor["modifier"],
        )
        if not _has_characteristic_modifier(node):
            raise SemanticNodeError(
                "attached characteristics require at least one modifier"
            )
        return node

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        if context.attached_object is None:
            return ()
        common = {
            "source_id": context.source_object_id,
            "timestamp": context.source_timestamp,
            "origin": ContinuousEffectOrigin.STATIC_ABILITY,
            "relation": ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,
            "related_object": context.attached_object,
            "applies": ObjectQuerySpec(
                zones=("battlefield",),
                types_all=node.subject_types_all,
            ),
        }
        effects: list[ContinuousEffect] = []
        if node.type_operations:
            effects.append(
                ContinuousEffect(
                    effect_id=f"{context.source_object_id}:{context.component_id}:4",
                    layer=Layer.TYPE,
                    sublayer="4",
                    operations=node.type_operations,
                    **common,
                )
            )
        if node.color_operations:
            effects.append(
                ContinuousEffect(
                    effect_id=f"{context.source_object_id}:{context.component_id}:5",
                    layer=Layer.COLOR,
                    sublayer="5",
                    operations=node.color_operations,
                    **common,
                )
            )
        ability_operations = (
            *((ContinuousOperation("remove_all_abilities"),) if node.remove_all_abilities else ()),
            *(
                ContinuousOperation("remove_ability", ability)
                for ability in node.remove_abilities
            ),
            *(
                ContinuousOperation("add_ability", ability)
                for ability in node.add_abilities
            ),
            *(
                ContinuousOperation("add_rules_text", line)
                for line in node.add_rules_text
            ),
            *(
                ContinuousOperation("add_ability_fragment", fragment)
                for fragment in node.add_ability_fragments
            ),
        )
        if ability_operations:
            effects.append(
                ContinuousEffect(
                    effect_id=f"{context.source_object_id}:{context.component_id}:6",
                    layer=Layer.ABILITY,
                    sublayer="6",
                    operations=ability_operations,
                    **common,
                )
            )
        if node.base_power is not None:
            effects.append(
                ContinuousEffect(
                    effect_id=f"{context.source_object_id}:{context.component_id}:7b",
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7b",
                    operations=(
                        ContinuousOperation(
                            "set_power_toughness",
                            [node.base_power, node.base_toughness],
                        ),
                    ),
                    **common,
                )
            )
        dynamic_power = 0
        dynamic_toughness = 0
        if node.quantity is not None and context.resolved_quantity is not None:
            dynamic_power = node.quantity_power * context.resolved_quantity
            dynamic_toughness = (
                node.quantity_toughness * context.resolved_quantity
            )
        if (
            node.power
            or node.toughness
            or dynamic_power
            or dynamic_toughness
        ):
            effects.append(
                ContinuousEffect(
                    effect_id=f"{context.source_object_id}:{context.component_id}:7c",
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    operations=(
                        ContinuousOperation(
                            "modify_power_toughness",
                            [
                                node.power + dynamic_power,
                                node.toughness + dynamic_toughness,
                            ],
                        ),
                    ),
                    **common,
                )
            )
        return tuple(effects)


__all__ = [
    "ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID",
    "AttachedFixedCharacteristicsHandler",
    "AttachedFixedCharacteristicsNode",
]
