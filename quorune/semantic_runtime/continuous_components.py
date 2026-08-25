from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Mapping, Protocol

from ..ability_fragments import (
    GrantedActivatedAbilitySpec,
    StaticAbilityFragment,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
)
from ..continuous_effects import (
    ContinuousEffect,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from ..mana import BASIC_LAND_MANA
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..rules.capabilities import load_default_capability_registry
from .component_registry import (
    RuntimeComponentRegistry,
    exact_fields,
    nonempty_strings,
)
from .context import SemanticNodeError


_FIXED_ANTHEM_HANDLER_ID = "continuous.anthem.power_toughness.v1"
_FIXED_QUERY_ANTHEM_HANDLER_ID = (
    "continuous.anthem.fixed-query.v2"
)
_BASIC_LAND_TYPE_HANDLER_ID = (
    "continuous.basic_land_type.add_all_lands.v1"
)
_FIXED_QUERY_ABILITY_GRANT_HANDLER_ID = (
    "continuous.ability.fixed-query-grant.v1"
)
_FIXED_QUERY_KEYWORD_GRANT_HANDLER_ID = (
    "continuous.ability.fixed-query-keyword-grant.v1"
)
_FIXED_QUERY_CHARACTERISTIC_GRANT_HANDLER_ID = (
    "continuous.characteristics.fixed-query-grant.v1"
)
_SUPPORTED_QUERY_KEYWORDS = frozenset(
    {
        "Deathtouch",
        "Defender",
        "Double Strike",
        "First Strike",
        "Flying",
        "Haste",
        "Hexproof",
        "Indestructible",
        "Infect",
        "Lifelink",
        "Menace",
        "Reach",
        "Shadow",
        "Shroud",
        "Trample",
        "Vigilance",
        "Wither",
    }
)


@dataclass(frozen=True, slots=True)
class FixedPowerToughnessAnthemNode:
    target_controller: str
    target_subtypes_all: tuple[str, ...]
    power: int
    toughness: int


@dataclass(frozen=True, slots=True)
class FixedQueryPowerToughnessAnthemNode:
    predicate: ObjectQuerySpec
    exclude_source: bool
    power: int
    toughness: int


@dataclass(frozen=True, slots=True)
class AddBasicLandTypeNode:
    target_types_all: tuple[str, ...]
    basic_land_type: str


@dataclass(frozen=True, slots=True)
class FixedQueryAbilityGrantNode:
    predicate: ObjectQuerySpec
    exclude_source: bool
    fragments: tuple[StaticAbilityFragment, ...]


@dataclass(frozen=True, slots=True)
class FixedQueryKeywordGrantNode:
    target_controller: str
    predicate: ObjectQuerySpec
    exclude_source: bool
    abilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FixedQueryCharacteristicGrantNode:
    target_controller: str
    predicate: ObjectQuerySpec
    exclude_source: bool
    abilities: tuple[str, ...]
    power: int
    toughness: int


@dataclass(frozen=True, slots=True)
class ContinuousEffectSourceContext:
    source_object_id: str
    source_ref: str
    source_controller: str
    source_timestamp: int
    component_id: str
    attached_object: ContinuousObjectIdentity | None = None

    def __post_init__(self) -> None:
        if not self.source_object_id or not self.source_ref:
            raise SemanticNodeError(
                "A continuous component source identity is required"
            )
        if not self.component_id:
            raise SemanticNodeError(
                "A continuous component identity is required"
            )
        if not self.source_controller:
            raise SemanticNodeError(
                "A continuous component source controller is required"
            )
        if self.source_timestamp < 0:
            raise SemanticNodeError(
                "A continuous component source timestamp cannot be negative"
            )
        if self.attached_object is not None and not isinstance(
            self.attached_object, ContinuousObjectIdentity
        ):
            raise SemanticNodeError(
                "An attached continuous component requires typed object identity"
            )


class ContinuousEffectComponentHandler(Protocol):
    handler_id: str
    schema_version: int
    family: str
    event: str
    rule_references: tuple[str, ...]
    capability_dependencies: tuple[str, ...]

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> Any: ...

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]: ...


@dataclass(frozen=True, slots=True)
class FixedPowerToughnessAnthemHandler:
    handler_id: str = _FIXED_ANTHEM_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.fixed_power_toughness_anthem"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "604.1",
        "611.3a",
        "613.1g",
        "613.4c",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.power_toughness.fixed_anthem",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedPowerToughnessAnthemNode:
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
            raise SemanticNodeError(
                f"{self.handler_id} must handle {self.event}"
            )
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError(
                "runtime handler condition must be an object"
            )
        exact_fields(
            condition,
            {"target_controller", "target_subtypes_all"},
            field="runtime handler condition",
        )
        target_controller = str(condition["target_controller"])
        if target_controller != "source_controller":
            raise SemanticNodeError(
                "fixed anthem currently requires "
                "target_controller=source_controller"
            )
        target_subtypes = tuple(
            subtype.casefold()
            for subtype in nonempty_strings(
                condition["target_subtypes_all"],
                field="condition.target_subtypes_all",
            )
        )
        if not target_subtypes:
            raise SemanticNodeError(
                "fixed anthem requires at least one target subtype"
            )
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError(
                "runtime handler modifier must be an object"
            )
        exact_fields(
            modifier,
            {"power", "toughness"},
            field="runtime handler modifier",
        )
        power = modifier["power"]
        toughness = modifier["toughness"]
        if type(power) is not int or type(toughness) is not int:
            raise SemanticNodeError(
                "fixed anthem modifiers must be integers"
            )
        if power == 0 and toughness == 0:
            raise SemanticNodeError(
                "fixed anthem must modify power or toughness"
            )
        return FixedPowerToughnessAnthemNode(
            target_controller=target_controller,
            target_subtypes_all=target_subtypes,
            power=power,
            toughness=toughness,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        return (
            ContinuousEffect(
                effect_id=(
                    f"{context.source_object_id}:{context.component_id}"
                ),
                source_id=context.source_object_id,
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7c",
                timestamp=context.source_timestamp,
                operations=(
                    ContinuousOperation(
                        "modify_power_toughness",
                        [node.power, node.toughness],
                    ),
                ),
                origin=ContinuousEffectOrigin.STATIC_ABILITY,
                applies=ObjectQuerySpec(
                    controller=context.source_controller,
                    subtypes_all=node.target_subtypes_all,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedQueryPowerToughnessAnthemHandler:
    """Closed CR 611.3 live-set anthem with a typed object predicate."""

    handler_id: str = _FIXED_QUERY_ANTHEM_HANDLER_ID
    schema_version: int = 2
    family: str = "continuous.fixed_query_power_toughness_anthem"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "604.1",
        "611.3a",
        "611.3b",
        "611.3c",
        "613.1g",
        "613.4c",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.power_toughness.fixed_anthem",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedQueryPowerToughnessAnthemNode:
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
            raise SemanticNodeError(
                f"{self.handler_id} must handle {self.event}"
            )
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError(
                "runtime handler condition must be an object"
            )
        exact_fields(
            condition,
            {"target_controller", "predicate", "exclude_source"},
            field="runtime handler condition",
        )
        if condition["target_controller"] != "source_controller":
            raise SemanticNodeError(
                "fixed query anthem requires source-controller targets"
            )
        if type(condition["exclude_source"]) is not bool:
            raise SemanticNodeError(
                "fixed query anthem exclude_source must be boolean"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(condition["predicate"])
        except ObjectQueryError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (
            predicate.owner is not None
            or predicate.controller is not None
            or predicate.exclude_ref is not None
            or predicate.known_to_actor is not None
        ):
            raise SemanticNodeError(
                "fixed query anthem reserves owner, controller, visibility, and source exclusion"
            )
        if predicate.zones not in {(), ("battlefield",)}:
            raise SemanticNodeError(
                "fixed query anthem applies only on the battlefield"
            )
        if "creature" not in predicate.types_all:
            raise SemanticNodeError(
                "fixed query anthem requires creature permanents"
            )
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError(
                "runtime handler modifier must be an object"
            )
        exact_fields(
            modifier,
            {"power", "toughness"},
            field="runtime handler modifier",
        )
        power = modifier["power"]
        toughness = modifier["toughness"]
        if type(power) is not int or type(toughness) is not int:
            raise SemanticNodeError(
                "fixed query anthem modifiers must be integers"
            )
        if power == 0 and toughness == 0:
            raise SemanticNodeError(
                "fixed query anthem must modify power or toughness"
            )
        return FixedQueryPowerToughnessAnthemNode(
            predicate=predicate,
            exclude_source=condition["exclude_source"],
            power=power,
            toughness=toughness,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        predicate = replace(
            node.predicate,
            zones=("battlefield",),
            controller=context.source_controller,
            exclude_ref=(context.source_ref if node.exclude_source else None),
        )
        return (
            ContinuousEffect(
                effect_id=(
                    f"{context.source_object_id}:{context.component_id}"
                ),
                source_id=context.source_object_id,
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7c",
                timestamp=context.source_timestamp,
                operations=(
                    ContinuousOperation(
                        "modify_power_toughness",
                        [node.power, node.toughness],
                    ),
                ),
                origin=ContinuousEffectOrigin.STATIC_ABILITY,
                applies=predicate,
            ),
        )


@dataclass(frozen=True, slots=True)
class AddBasicLandTypeHandler:
    handler_id: str = _BASIC_LAND_TYPE_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.basic_land_type.add_all_lands"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "305.6",
        "305.7",
        "613.1d",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.basic_land_type.add_all_lands",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> AddBasicLandTypeNode:
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
            raise SemanticNodeError(
                f"{self.handler_id} must handle {self.event}"
            )
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError(
                "runtime handler condition must be an object"
            )
        exact_fields(
            condition,
            {"target_types_all"},
            field="runtime handler condition",
        )
        target_types = tuple(
            value.casefold()
            for value in nonempty_strings(
                condition["target_types_all"],
                field="condition.target_types_all",
            )
        )
        if target_types != ("land",):
            raise SemanticNodeError(
                "basic-land-type addition currently requires all lands"
            )
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError(
                "runtime handler modifier must be an object"
            )
        exact_fields(
            modifier,
            {"basic_land_type"},
            field="runtime handler modifier",
        )
        subtype = str(modifier["basic_land_type"]).casefold()
        if subtype not in BASIC_LAND_MANA:
            raise SemanticNodeError(
                "basic_land_type must name a basic land type"
            )
        return AddBasicLandTypeNode(
            target_types_all=target_types,
            basic_land_type=subtype,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        return (
            ContinuousEffect(
                effect_id=(
                    f"{context.source_object_id}:{context.component_id}"
                ),
                source_id=context.source_object_id,
                layer=Layer.TYPE,
                sublayer="4",
                timestamp=context.source_timestamp,
                operations=(
                    ContinuousOperation(
                        "add_types",
                        [node.basic_land_type],
                        field="subtypes",
                    ),
                ),
                origin=ContinuousEffectOrigin.STATIC_ABILITY,
                applies=ObjectQuerySpec(types_all=node.target_types_all),
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedQueryAbilityGrantHandler:
    """Grant closed typed ability fragments to a live queried object set."""

    handler_id: str = _FIXED_QUERY_ABILITY_GRANT_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.fixed_query_ability_grant"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "604.1",
        "611.3a",
        "611.3b",
        "611.3c",
        "613.1f",
        "613.6",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.ability.fixed_query_grant",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedQueryAbilityGrantNode:
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
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError("runtime handler condition must be an object")
        exact_fields(
            condition,
            {"target_controller", "predicate", "exclude_source"},
            field="runtime handler condition",
        )
        if condition["target_controller"] != "source_controller":
            raise SemanticNodeError(
                "fixed query ability grants require source-controller targets"
            )
        if type(condition["exclude_source"]) is not bool:
            raise SemanticNodeError(
                "fixed query ability grant exclude_source must be boolean"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(condition["predicate"])
        except ObjectQueryError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (
            predicate.owner is not None
            or predicate.controller is not None
            or predicate.exclude_ref is not None
            or predicate.known_to_actor is not None
        ):
            raise SemanticNodeError(
                "fixed query ability grants reserve owner, controller, visibility, "
                "and source exclusion"
            )
        if predicate.zones not in {(), ("battlefield",)}:
            raise SemanticNodeError(
                "fixed query ability grants apply only on the battlefield"
            )
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError("runtime handler modifier must be an object")
        exact_fields(
            modifier,
            {"add_ability_fragments"},
            field="runtime handler modifier",
        )
        raw_fragments = modifier["add_ability_fragments"]
        if not isinstance(raw_fragments, list) or not raw_fragments:
            raise SemanticNodeError(
                "fixed query ability grants require a nonempty fragment array"
            )
        try:
            fragments = tuple(
                ability_fragment_from_dict(value) for value in raw_fragments
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(
                "fixed query ability grant fragments are malformed"
            ) from exc
        if any(
            not isinstance(fragment, GrantedActivatedAbilitySpec)
            or not fragment.mana_ability
            or not fragment.fixed_mana_outputs
            for fragment in fragments
        ):
            raise SemanticNodeError(
                "fixed query ability grants currently require typed fixed-output "
                "mana abilities"
            )
        return FixedQueryAbilityGrantNode(
            predicate=predicate,
            exclude_source=condition["exclude_source"],
            fragments=fragments,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        predicate = replace(
            node.predicate,
            zones=("battlefield",),
            controller=context.source_controller,
            exclude_ref=(context.source_ref if node.exclude_source else None),
        )
        return (
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}",
                source_id=context.source_object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=context.source_timestamp,
                operations=tuple(
                    ContinuousOperation(
                        "add_ability_fragment",
                        ability_fragment_to_dict(fragment),
                    )
                    for fragment in node.fragments
                ),
                origin=ContinuousEffectOrigin.STATIC_ABILITY,
                applies=predicate,
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedQueryKeywordGrantHandler:
    """Grant a closed keyword set to a live queried object set."""

    handler_id: str = _FIXED_QUERY_KEYWORD_GRANT_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.fixed_query_keyword_grant"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "604.1",
        "611.3a",
        "611.3b",
        "611.3c",
        "613.1f",
        "613.6",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.ability.fixed_query_keyword_grant",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedQueryKeywordGrantNode:
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
        if (
            type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
        ):
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(f"{self.handler_id} must handle {self.event}")
        condition = descriptor["condition"]
        if not isinstance(condition, Mapping):
            raise SemanticNodeError("runtime handler condition must be an object")
        exact_fields(
            condition,
            {"target_controller", "predicate", "exclude_source"},
            field="runtime handler condition",
        )
        if condition["target_controller"] not in {
            "any",
            "source_controller",
        }:
            raise SemanticNodeError(
                "fixed query keyword grants require a closed controller relation"
            )
        if type(condition["exclude_source"]) is not bool:
            raise SemanticNodeError(
                "fixed query keyword grant exclude_source must be boolean"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(condition["predicate"])
        except ObjectQueryError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (
            predicate.owner is not None
            or predicate.controller is not None
            or predicate.exclude_ref is not None
            or predicate.known_to_actor is not None
        ):
            raise SemanticNodeError(
                "fixed query keyword grants reserve owner, controller, visibility, "
                "and source exclusion"
            )
        if predicate.zones not in {(), ("battlefield",)}:
            raise SemanticNodeError(
                "fixed query keyword grants apply only on the battlefield"
            )
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError("runtime handler modifier must be an object")
        exact_fields(
            modifier,
            {"add_abilities"},
            field="runtime handler modifier",
        )
        abilities = nonempty_strings(
            modifier["add_abilities"],
            field="modifier.add_abilities",
        )
        if (
            not abilities
            or len(set(abilities)) != len(abilities)
            or any(ability not in _SUPPORTED_QUERY_KEYWORDS for ability in abilities)
        ):
            raise SemanticNodeError(
                "fixed query keyword grants require unique supported keywords"
            )
        return FixedQueryKeywordGrantNode(
            target_controller=condition["target_controller"],
            predicate=predicate,
            exclude_source=condition["exclude_source"],
            abilities=abilities,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        predicate = replace(
            node.predicate,
            zones=("battlefield",),
            controller=(
                context.source_controller
                if node.target_controller == "source_controller"
                else None
            ),
            exclude_ref=(context.source_ref if node.exclude_source else None),
        )
        return (
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}",
                source_id=context.source_object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=context.source_timestamp,
                operations=tuple(
                    ContinuousOperation("add_ability", ability)
                    for ability in node.abilities
                ),
                origin=ContinuousEffectOrigin.STATIC_ABILITY,
                applies=predicate,
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedQueryCharacteristicGrantHandler:
    """Grant fixed keywords and P/T to one shared live object query."""

    handler_id: str = _FIXED_QUERY_CHARACTERISTIC_GRANT_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.fixed_query_characteristic_grant"
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
        "continuous.ability.fixed_query_keyword_grant",
        "continuous.power_toughness.fixed_anthem",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedQueryCharacteristicGrantNode:
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
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError("runtime handler modifier must be an object")
        exact_fields(
            modifier,
            {"add_abilities", "power", "toughness"},
            field="runtime handler modifier",
        )
        keyword_node = FixedQueryKeywordGrantHandler().validate(
            {
                "handler_id": _FIXED_QUERY_KEYWORD_GRANT_HANDLER_ID,
                "schema_version": 1,
                "event": self.event,
                "condition": descriptor["condition"],
                "modifier": {
                    "add_abilities": modifier["add_abilities"],
                },
            }
        )
        anthem_node = FixedQueryPowerToughnessAnthemHandler().validate(
            {
                "handler_id": _FIXED_QUERY_ANTHEM_HANDLER_ID,
                "schema_version": 2,
                "event": self.event,
                "condition": descriptor["condition"],
                "modifier": {
                    "power": modifier["power"],
                    "toughness": modifier["toughness"],
                },
            }
        )
        return FixedQueryCharacteristicGrantNode(
            target_controller=keyword_node.target_controller,
            predicate=keyword_node.predicate,
            exclude_source=keyword_node.exclude_source,
            abilities=keyword_node.abilities,
            power=anthem_node.power,
            toughness=anthem_node.toughness,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        predicate = replace(
            node.predicate,
            zones=("battlefield",),
            controller=context.source_controller,
            exclude_ref=(context.source_ref if node.exclude_source else None),
        )
        common = {
            "source_id": context.source_object_id,
            "timestamp": context.source_timestamp,
            "origin": ContinuousEffectOrigin.STATIC_ABILITY,
            "applies": predicate,
        }
        return (
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
            ),
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
            ),
        )


class ContinuousEffectComponentRegistry(
    RuntimeComponentRegistry[
        ContinuousEffectSourceContext,
        ContinuousEffect,
    ]
):
    pass


@lru_cache(maxsize=1)
def default_continuous_effect_component_registry(
) -> ContinuousEffectComponentRegistry:
    from .attached_continuous import AttachedFixedCharacteristicsHandler

    registry = ContinuousEffectComponentRegistry(
        (
            FixedPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(),
            AddBasicLandTypeHandler(),
            FixedQueryAbilityGrantHandler(),
            FixedQueryKeywordGrantHandler(),
            FixedQueryCharacteristicGrantHandler(),
            AttachedFixedCharacteristicsHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()
