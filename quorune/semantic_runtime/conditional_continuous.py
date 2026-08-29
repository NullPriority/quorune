from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionError,
    FixedPublicStateConditionSpec,
)
from ..continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectOrigin,
    ContinuousEffectRelation,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from .component_registry import exact_fields, nonempty_strings
from .context import SemanticNodeError
from .continuous_components import (
    ContinuousEffectSourceContext,
    _fixed_query_effect_predicate,
)


@dataclass(frozen=True, slots=True)
class FixedPublicStateCharacteristicsNode:
    source_condition: FixedPublicStateConditionSpec
    target_kind: str
    target_controller: str | None
    predicate: ObjectQuerySpec | None
    exclude_source: bool
    subject_types_all: tuple[str, ...]
    abilities: tuple[str, ...]
    power: int
    toughness: int


def _validate_target(
    value: Any,
) -> tuple[
    str,
    str | None,
    ObjectQuerySpec | None,
    bool,
    tuple[str, ...],
]:
    if not isinstance(value, Mapping):
        raise SemanticNodeError("runtime handler target must be an object")
    exact_fields(
        value,
        {
            "kind",
            "target_controller",
            "predicate",
            "exclude_source",
            "types_all",
        },
        field="runtime handler target",
    )
    target_kind = value["kind"]
    if target_kind not in {"attached", "fixed_query", "source"}:
        raise SemanticNodeError(
            "fixed public-state characteristics require a closed target kind"
        )
    if type(value["exclude_source"]) is not bool:
        raise SemanticNodeError(
            "fixed public-state source exclusion must be boolean"
        )
    raw_types = value["types_all"]
    if not isinstance(raw_types, list) or any(
        type(item) is not str or not item.strip() for item in raw_types
    ):
        raise SemanticNodeError(
            "fixed public-state target types must be strings"
        )
    subject_types_all = tuple(sorted(item.casefold() for item in raw_types))
    if len(set(subject_types_all)) != len(subject_types_all):
        raise SemanticNodeError(
            "fixed public-state target types must be unique"
        )

    predicate = None
    target_controller = value["target_controller"]
    if target_kind == "fixed_query":
        if target_controller not in {
            "any",
            "source_controller",
            "source_opponents",
        }:
            raise SemanticNodeError(
                "fixed queries require a closed controller relation"
            )
        if subject_types_all:
            raise SemanticNodeError(
                "fixed queries cannot carry attached subject types"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(value["predicate"])
        except (ObjectQueryError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (
            predicate.owner is not None
            or predicate.controller is not None
            or predicate.excluded_controllers
            or predicate.exclude_ref is not None
            or predicate.known_to_actor is not None
        ):
            raise SemanticNodeError(
                "fixed public-state queries reserve owner, controller, "
                "visibility, and source exclusion"
            )
        if predicate.zones not in {(), ("battlefield",)}:
            raise SemanticNodeError(
                "fixed public-state queries are battlefield-only"
            )
    elif target_kind == "attached":
        if (
            target_controller is not None
            or value["predicate"] is not None
            or value["exclude_source"]
            or not subject_types_all
            or set(subject_types_all) - {"creature", "land"}
        ):
            raise SemanticNodeError(
                "attached public-state targets require only creature or land types"
            )
    elif (
        target_controller is not None
        or value["predicate"] is not None
        or value["exclude_source"]
        or subject_types_all
    ):
        raise SemanticNodeError(
            "source public-state targets cannot carry query fields"
        )
    return (
        target_kind,
        target_controller,
        predicate,
        value["exclude_source"],
        subject_types_all,
    )


def _validate_modifier(value: Any) -> tuple[tuple[str, ...], int, int]:
    if not isinstance(value, Mapping):
        raise SemanticNodeError("runtime handler modifier must be an object")
    exact_fields(
        value,
        {"add_abilities", "power", "toughness"},
        field="runtime handler modifier",
    )
    abilities = nonempty_strings(
        value["add_abilities"],
        field="modifier.add_abilities",
    )
    if any(
        ability not in FIXED_CHARACTERISTIC_KEYWORDS
        for ability in abilities
    ):
        raise SemanticNodeError(
            "fixed public-state grants require supported keywords"
        )
    power = value["power"]
    toughness = value["toughness"]
    if type(power) is not int or type(toughness) is not int:
        raise SemanticNodeError(
            "fixed public-state power/toughness modifiers must be integers"
        )
    if not abilities and power == 0 and toughness == 0:
        raise SemanticNodeError(
            "fixed public-state characteristics require a modifier"
        )
    return abilities, power, toughness


@dataclass(frozen=True, slots=True)
class FixedPublicStateCharacteristicsHandler:
    """Apply fixed layer-6/7c results behind one public-state condition."""

    handler_id: str = FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.fixed_public_state"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "604.1",
        "611.3a",
        "611.3b",
        "611.3c",
        "613.1f",
        "613.1g",
        "613.4c",
        "613.6",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.characteristics.fixed_public_state",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedPublicStateCharacteristicsNode:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                "source_condition",
                "target",
                "modifier",
            },
            field="runtime handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Runtime handler ID does not match registry")
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(f"{self.handler_id} must handle {self.event}")
        try:
            source_condition = FixedPublicStateConditionSpec.from_dict(
                descriptor["source_condition"]
            )
        except (FixedPublicStateConditionError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

        (
            target_kind,
            target_controller,
            predicate,
            exclude_source,
            subject_types_all,
        ) = _validate_target(descriptor["target"])
        abilities, power, toughness = _validate_modifier(
            descriptor["modifier"]
        )
        return FixedPublicStateCharacteristicsNode(
            source_condition=source_condition,
            target_kind=target_kind,
            target_controller=target_controller,
            predicate=predicate,
            exclude_source=exclude_source,
            subject_types_all=subject_types_all,
            abilities=abilities,
            power=power,
            toughness=toughness,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        snapshot = context.public_state
        if snapshot is None:
            raise SemanticNodeError(
                "fixed public-state characteristics require a state snapshot"
            )
        if not node.source_condition.matches(snapshot):
            return ()

        relation = ContinuousEffectRelation.NONE
        related_object = None
        if node.target_kind == "source":
            if not context.source_logical_object_id:
                raise SemanticNodeError(
                    "source characteristics require logical object identity"
                )
            relation = ContinuousEffectRelation.SOURCE_OBJECT
            related_object = ContinuousObjectIdentity(
                object_id=context.source_object_id,
                logical_object_id=context.source_logical_object_id,
            )
            predicate = ObjectQuerySpec(zones=("battlefield",))
        elif node.target_kind == "attached":
            if context.attached_object is None:
                return ()
            relation = ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT
            related_object = context.attached_object
            predicate = ObjectQuerySpec(
                zones=("battlefield",),
                types_all=node.subject_types_all,
            )
        else:
            assert node.predicate is not None
            predicate = _fixed_query_effect_predicate(
                node.predicate,
                target_controller=str(node.target_controller),
                exclude_source=node.exclude_source,
                context=context,
            )

        common = {
            "source_id": context.source_object_id,
            "timestamp": context.source_timestamp,
            "origin": ContinuousEffectOrigin.STATIC_ABILITY,
            "applies": predicate,
            "relation": relation,
            "related_object": related_object,
        }
        effects: list[ContinuousEffect] = []
        if node.abilities:
            effects.append(
                ContinuousEffect(
                    effect_id=(
                        f"{context.source_object_id}:{context.component_id}:6"
                    ),
                    layer=Layer.ABILITY,
                    sublayer="6",
                    operations=tuple(
                        ContinuousOperation("add_ability", ability)
                        for ability in node.abilities
                    ),
                    **common,
                )
            )
        if node.power or node.toughness:
            effects.append(
                ContinuousEffect(
                    effect_id=(
                        f"{context.source_object_id}:{context.component_id}:7c"
                    ),
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    operations=(
                        ContinuousOperation(
                            "modify_power_toughness",
                            [node.power, node.toughness],
                        ),
                    ),
                    **common,
                )
            )
        return tuple(effects)


__all__ = [
    "FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID",
    "FixedPublicStateCharacteristicsHandler",
    "FixedPublicStateCharacteristicsNode",
]
