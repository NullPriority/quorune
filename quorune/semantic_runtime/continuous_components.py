from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Mapping, Protocol

from ..ability_fragments import (
    GrantedActivatedAbilitySpec,
    GrantedTriggeredAbilitySpec,
    StaticComponentSpec,
    StaticAbilityFragment,
    ability_fragment_to_dict,
    ability_fragment_from_dict,
)
from ..continuous_effects import (
    ContinuousEffect,
    ContinuousEffectOrigin,
    ContinuousEffectRelation,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS, triggered_keyword_grant
from ..ability_fragments import ProtectionSpec
from ..mana import BASIC_LAND_MANA
from ..leveler_bands import (
    LEVELER_BANDS_CAPABILITY_ID,
    LEVELER_BANDS_HANDLER_ID,
    LevelerBandError,
    LevelerBandsSpec,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import ObjectQueryResult
from ..object_query_union import bind_receiver_union_source, validate_receiver_union_template
from ..counter_names import normalized_counter_name
from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap
from ..continuous_conditions import FixedPublicStateConditionSnapshot
from ..defender_permission import DefenderAttackPermission
from ..counter_snapshot import permanent_counter_total, COUNTER_TOTAL_CAPABILITY
from ..block_capacity_model import BlockCapacitySpec
from ..as_unblocked import AsUnblockedAssignmentPermission
from ..declaration_fragments import (
    DeclarationRequirementTemplate,
    DeclarationRestrictionTemplate,
)
from ..rules.capabilities import load_default_capability_registry
from ..trigger_participation import WardSpec
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
@dataclass(frozen=True, slots=True)
class FixedPowerToughnessAnthemNode:
    target_controller: str
    target_subtypes_all: tuple[str, ...]
    power: int
    toughness: int


@dataclass(frozen=True, slots=True)
class FixedQueryPowerToughnessAnthemNode:
    target_controller: str
    predicate: ObjectQuerySpec
    exclude_source: bool
    power: int
    toughness: int
    source_counter: str | None = None
    source_counter_total: bool = False


@dataclass(frozen=True, slots=True)
class AddBasicLandTypeNode:
    target_types_all: tuple[str, ...]
    basic_land_type: str


@dataclass(frozen=True, slots=True)
class FixedQueryAbilityGrantNode:
    target_controller: str
    predicate: ObjectQuerySpec
    exclude_source: bool
    abilities: tuple[str, ...]
    fragments: tuple[StaticAbilityFragment, ...]


@dataclass(frozen=True, slots=True)
class FixedQueryKeywordGrantNode:
    target_controller: str
    predicate: ObjectQuerySpec
    exclude_source: bool
    abilities: tuple[str, ...]
    fragments: tuple[StaticAbilityFragment, ...] = ()


@dataclass(frozen=True, slots=True)
class FixedQueryCharacteristicGrantNode:
    target_controller: str
    predicate: ObjectQuerySpec
    exclude_source: bool
    abilities: tuple[str, ...]
    fragments: tuple[StaticAbilityFragment, ...]
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
    source_logical_object_id: str | None = None
    public_state: FixedPublicStateConditionSnapshot | None = None
    resolved_quantity: int | None = None
    source_counters: FrozenMap = FrozenMap()
    source_designations: FrozenMap = FrozenMap()
    paired_object: ContinuousObjectIdentity | None = None
    public_type_objects: tuple[ObjectQueryResult, ...] | None = None
    enchanted_player: str | None = None

    def __post_init__(self) -> None:
        if self.enchanted_player is not None and (type(self.enchanted_player) is not str
                or not self.enchanted_player or self.enchanted_player.strip()!=self.enchanted_player):
            raise SemanticNodeError('Enchanted-player context requires a canonical player identity')
        if self.public_type_objects is not None and (not isinstance(self.public_type_objects,tuple)
                or any(not isinstance(row,ObjectQueryResult) for row in self.public_type_objects)):
            raise SemanticNodeError('Public type snapshots require an immutable tuple of typed object facts')
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
        if self.paired_object is not None and not isinstance(self.paired_object, ContinuousObjectIdentity):
            raise SemanticNodeError('A paired component requires typed object identity')
        if self.source_logical_object_id is not None and (
            type(self.source_logical_object_id) is not str
            or not self.source_logical_object_id
        ):
            raise SemanticNodeError(
                "A continuous component logical source identity must be nonempty"
            )
        if self.public_state is not None and not isinstance(
            self.public_state, FixedPublicStateConditionSnapshot
        ):
            raise SemanticNodeError(
                "A continuous component public-state snapshot must be typed"
            )
        if self.resolved_quantity is not None and (
            type(self.resolved_quantity) is not int
            or self.resolved_quantity < 0
        ):
            raise SemanticNodeError(
                "A continuous component quantity must be a nonnegative integer"
            )
        if not isinstance(self.source_counters, FrozenMap):
            try:
                object.__setattr__(
                    self,
                    "source_counters",
                    FrozenMap(self.source_counters),
                )
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(
                    "Continuous component source counters must be immutable"
                ) from exc
        if any(
            type(value) is not int or value < 0
            for value in self.source_counters.values()
        ):
            raise SemanticNodeError(
                "Continuous component source counters must be nonnegative integers"
            )


def _fixed_query_effect_predicate(
    predicate: ObjectQuerySpec,
    *,
    target_controller: str,
    exclude_source: bool,
    context: ContinuousEffectSourceContext,
) -> ObjectQuerySpec:
    """Bind one validated controller relation to a canonical live query."""

    return replace(
        bind_receiver_union_source(predicate, context.source_ref),
        zones=("battlefield",),
        owner=context.source_controller if target_controller=='source_owner' else None,
        controller=(
            context.source_controller
            if target_controller == "source_controller"
            else None
        ),
        excluded_controllers=(
            (context.source_controller,)
            if target_controller == "source_opponents"
            else ()
        ),
        exclude_ref=(context.source_ref if exclude_source else None),
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
        version = descriptor["schema_version"]
        if type(version) is not int or version != self.schema_version:
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
        if condition["target_controller"] not in {
            "any",
            "source_controller",
            "source_opponents",
            "source_owner",
        }:
            raise SemanticNodeError(
                "fixed query anthem requires a closed controller relation"
            )
        if type(condition["exclude_source"]) is not bool:
            raise SemanticNodeError(
                "fixed query anthem exclude_source must be boolean"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(condition["predicate"])
            validate_receiver_union_template(predicate)
        except ObjectQueryError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (condition['target_controller']=='source_owner' or predicate.commander is not None) and (predicate.commander is not True or predicate.types_all!=('creature',) or condition['target_controller'] not in {'source_owner','source_controller'}):
            raise SemanticNodeError('Owner-relative characteristic grants require designated commander creatures')
        if (
            predicate.owner is not None
            or predicate.controller is not None
            or predicate.excluded_controllers
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
        if not (
            "creature" in predicate.types_all
            or (not predicate.types_all and (predicate.subtypes_all or predicate.subtypes_any)
                and all(canonical_creature_subtype(value) == value
                        for value in (*predicate.subtypes_all, *predicate.subtypes_any)))
            or (predicate.any_of and all(
                'creature' in part.types_all or (not part.types_all and part.subtypes_all
                    and all(canonical_creature_subtype(value) == value for value in part.subtypes_all))
                for part in predicate.any_of))
        ):
            raise SemanticNodeError(
                "fixed query anthem requires creatures or a canonical creature-subtype set"
            )
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError(
                "runtime handler modifier must be an object"
            )
        exact_fields(
            modifier,
            {"power", "toughness", *(("source_counter",) if version == 3 else ("source_counter_total",) if version == 4 else ())},
            field="runtime handler modifier",
        )
        power = modifier["power"]
        toughness = modifier["toughness"]
        if type(power) is not int or type(toughness) is not int:
            raise SemanticNodeError(
                "fixed query anthem modifiers must be integers"
            )
        if version == 4 and modifier['source_counter_total'] is not True:
            raise SemanticNodeError('Total-counter anthem requires its explicit true selector')
        counter = None
        if version == 3:
            counter = modifier['source_counter']
            if type(counter) is not str:
                raise SemanticNodeError("Scaled anthem requires a named source counter")
            try:
                normalized = normalized_counter_name(counter)
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
            if normalized != counter:
                raise SemanticNodeError("Scaled anthem source counter must be canonical")
        if power == 0 and toughness == 0:
            raise SemanticNodeError(
                "fixed query anthem must modify power or toughness"
            )
        return FixedQueryPowerToughnessAnthemNode(
            target_controller=condition["target_controller"],
            predicate=predicate,
            exclude_source=condition["exclude_source"],
            power=power,
            toughness=toughness,
            source_counter=counter,
            source_counter_total=version == 4,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        multiplier = (permanent_counter_total(context.source_counters) if node.source_counter_total
            else context.source_counters.get(node.source_counter, 0) if node.source_counter is not None else 1)
        predicate = _fixed_query_effect_predicate(
            node.predicate,
            target_controller=node.target_controller,
            exclude_source=node.exclude_source,
            context=context,
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
                        [node.power * multiplier, node.toughness * multiplier],
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
        if condition["target_controller"] not in {
            "source_controller",
            "source_opponents",
            "any",
            "source_owner",
        }:
            raise SemanticNodeError(
                "fixed query ability grants require a supported public controller relation"
            )
        if type(condition["exclude_source"]) is not bool:
            raise SemanticNodeError(
                "fixed query ability grant exclude_source must be boolean"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(condition["predicate"])
            validate_receiver_union_template(predicate)
        except ObjectQueryError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (condition['target_controller']=='source_owner' or predicate.commander is not None) and (predicate.commander is not True or predicate.types_all!=('creature',) or condition['target_controller'] not in {'source_owner','source_controller'}):
            raise SemanticNodeError('Owner-relative ability grants require designated commander creatures')
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
        modifier_fields = set(modifier)
        if modifier_fields not in (
            {"add_ability_fragments"},
            {"add_abilities", "add_ability_fragments"},
        ):
            raise SemanticNodeError(
                "fixed query ability grants have a closed modifier schema"
            )
        abilities: tuple[str, ...] = ()
        if "add_abilities" in modifier:
            abilities = nonempty_strings(
                modifier["add_abilities"],
                field="modifier.add_abilities",
            )
            if (
                len(set(abilities)) != len(abilities)
                or any(
                    ability not in FIXED_CHARACTERISTIC_KEYWORDS
                    for ability in abilities
                )
            ):
                raise SemanticNodeError(
                    "fixed query ability grants require unique supported keywords"
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
            not isinstance(
                fragment,
                (
                    DeclarationRequirementTemplate,
                    DeclarationRestrictionTemplate,
                    DefenderAttackPermission,
                    BlockCapacitySpec,
                    AsUnblockedAssignmentPermission,
                    GrantedActivatedAbilitySpec,
                    GrantedTriggeredAbilitySpec,
                    StaticComponentSpec,
                ),
            )
            for fragment in fragments
        ):
            raise SemanticNodeError(
                "fixed query ability grants require typed declaration, activated, "
                "triggered abilities or versioned static cost components"
            )
        if any(isinstance(fragment, StaticComponentSpec) and fragment.schema_version not in {2, 3} for fragment in fragments):
            raise SemanticNodeError('Static grants require a versioned cost component reference')
        if any(isinstance(fragment, StaticComponentSpec) and fragment.schema_version == 3 and fragment.grant_timestamp is not None for fragment in fragments):
            raise SemanticNodeError('A characteristic grant descriptor cannot prebind its effect timestamp')
        return FixedQueryAbilityGrantNode(
            target_controller=condition["target_controller"],
            predicate=predicate,
            exclude_source=condition["exclude_source"],
            abilities=abilities,
            fragments=fragments,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        predicate = _fixed_query_effect_predicate(
            node.predicate,
            target_controller=node.target_controller,
            exclude_source=node.exclude_source,
            context=context,
        )
        return (
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}",
                source_id=context.source_object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=context.source_timestamp,
                operations=(
                    *(
                        ContinuousOperation("add_ability", ability)
                        for ability in node.abilities
                    ),
                    *(
                        ContinuousOperation(
                            "add_ability_fragment",
                            ability_fragment_to_dict(fragment),
                        )
                        for fragment in node.fragments
                    ),
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
            "source_opponents",
            "source_owner",
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
            validate_receiver_union_template(predicate)
        except ObjectQueryError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (condition['target_controller']=='source_owner' or predicate.commander is not None) and (predicate.commander is not True or predicate.types_all!=('creature',) or condition['target_controller'] not in {'source_owner','source_controller'}):
            raise SemanticNodeError('Owner-relative keyword grants require designated commander creatures')
        if (
            predicate.owner is not None
            or predicate.controller is not None
            or predicate.excluded_controllers
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
        modifier_fields = set(modifier)
        if modifier_fields not in (
            {"add_abilities"},
            {"add_abilities", "add_ability_fragments"},
        ):
            raise SemanticNodeError(
                "fixed query keyword grants have a closed modifier schema"
            )
        abilities = nonempty_strings(
            modifier["add_abilities"],
            field="modifier.add_abilities",
        )
        raw_fragments = modifier.get("add_ability_fragments", ())
        if not isinstance(raw_fragments, (list, tuple)):
            raise SemanticNodeError(
                "fixed query keyword grant fragments must be an array"
            )
        try:
            fragments = tuple(
                ability_fragment_from_dict(value) for value in raw_fragments
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(
                "fixed query keyword grant fragments are malformed"
            ) from exc
        expected_triggered = tuple(
            triggered[1] for ability in abilities
            if (triggered := triggered_keyword_grant(ability)) is not None
        )
        actual_triggered = tuple(
            ability_fragment_to_dict(fragment) for fragment in fragments
            if not isinstance(fragment, (WardSpec, ProtectionSpec))
        )
        if actual_triggered != expected_triggered or any(
            isinstance(fragment, WardSpec) and fragment.generic_cost is None
            for fragment in fragments
        ):
            raise SemanticNodeError('Keyword grant fragments must exactly match their typed keyword labels')
        ward_labels = tuple(
            f"Ward {{{fragment.generic_cost}}}" for fragment in fragments if isinstance(fragment, WardSpec)
        )
        ordinary = tuple(
            ability for ability in abilities if ability != 'Protection'
            and not ability.startswith("Ward {") and triggered_keyword_grant(ability) is None
        )
        protection = tuple(fragment for fragment in fragments if isinstance(fragment, ProtectionSpec))
        if protection:
            if descriptor['schema_version'] != 2 or abilities != ('Protection',) or len(protection) != len(fragments):
                raise SemanticNodeError('Query protection requires its complete typed version-two modifier')
            if len({str(ability_fragment_to_dict(fragment)) for fragment in protection}) != len(protection):
                raise SemanticNodeError('Query protection fragments must be distinct')
        elif 'Protection' in abilities or descriptor['schema_version'] == 2:
            raise SemanticNodeError('Query protection has no complete typed fragment')
        if (
            not abilities
            or len(set(abilities)) != len(abilities)
            or tuple(
                ability for ability in abilities if ability.startswith("Ward {")
            )
            != ward_labels
            or any(
                ability not in FIXED_CHARACTERISTIC_KEYWORDS
                for ability in ordinary
            )
        ):
            raise SemanticNodeError(
                "fixed query keyword grants require unique supported keywords"
            )
        return FixedQueryKeywordGrantNode(
            target_controller=condition["target_controller"],
            predicate=predicate,
            exclude_source=condition["exclude_source"],
            abilities=abilities,
            fragments=fragments,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        predicate = _fixed_query_effect_predicate(
            node.predicate,
            target_controller=node.target_controller,
            exclude_source=node.exclude_source,
            context=context,
        )
        return (
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}",
                source_id=context.source_object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=context.source_timestamp,
                operations=tuple(
                    (
                        ContinuousOperation("add_ability", ability)
                        for ability in node.abilities
                    )
                )
                + tuple(
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
        if type(descriptor['schema_version']) is not int or descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                f"Unsupported {self.handler_id} schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(f"{self.handler_id} must handle {self.event}")
        modifier = descriptor["modifier"]
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError("runtime handler modifier must be an object")
        modifier_fields = set(modifier)
        if modifier_fields not in (
            {"add_abilities", "power", "toughness"},
            {
                "add_abilities",
                "add_ability_fragments",
                "power",
                "toughness",
            },
        ):
            raise SemanticNodeError(
                "fixed query characteristic grants have a closed modifier schema"
            )
        keyword_handler = FixedQueryProtectionGrantHandler() if self.schema_version == 2 else FixedQueryKeywordGrantHandler()
        keyword_node = keyword_handler.validate(
            {
                "handler_id": keyword_handler.handler_id,
                "schema_version": keyword_handler.schema_version,
                "event": self.event,
                "condition": descriptor["condition"],
                "modifier": {
                    "add_abilities": modifier["add_abilities"],
                    **(
                        {
                            "add_ability_fragments": modifier[
                                "add_ability_fragments"
                            ]
                        }
                        if "add_ability_fragments" in modifier
                        else {}
                    ),
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
            fragments=keyword_node.fragments,
            power=anthem_node.power,
            toughness=anthem_node.toughness,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        node = self.validate(descriptor)
        predicate = _fixed_query_effect_predicate(
            node.predicate,
            target_controller=node.target_controller,
            exclude_source=node.exclude_source,
            context=context,
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
                    (
                        ContinuousOperation("add_ability", ability)
                        for ability in node.abilities
                    )
                )
                + tuple(
                    ContinuousOperation(
                        "add_ability_fragment",
                        ability_fragment_to_dict(fragment),
                    )
                    for fragment in node.fragments
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


@dataclass(frozen=True, slots=True)
class FixedQueryProtectionGrantHandler(FixedQueryKeywordGrantHandler):
    handler_id: str = 'continuous.ability.fixed-query-protection-grant.v2'
    schema_version: int = 2
    capability_dependencies: tuple[str, ...] = ('continuous.ability.fixed_query_keyword_grant', 'protection.typed.debt')


@dataclass(frozen=True, slots=True)
class FixedQueryCharacteristicProtectionGrantHandler(FixedQueryCharacteristicGrantHandler):
    handler_id: str = 'continuous.characteristics.fixed-query-protection-grant.v2'
    schema_version: int = 2
    capability_dependencies: tuple[str, ...] = ('continuous.ability.fixed_query_keyword_grant',
                                               'continuous.power_toughness.fixed_anthem', 'protection.typed.debt')


@dataclass(frozen=True, slots=True)
class LevelerBandsHandler:
    """Apply current Leveler abilities in layer 6 and base P/T in 7b."""

    handler_id: str = LEVELER_BANDS_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.leveler_bands"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = (
        "711.2",
        "711.2a",
        "711.2b",
        "711.4",
        "711.5",
        "711.6",
    )
    capability_dependencies: tuple[str, ...] = (
        LEVELER_BANDS_CAPABILITY_ID,
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> LevelerBandsSpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "bands"},
            field="Leveler-band handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Leveler-band handler identity changed")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported Leveler-band handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Leveler bands must handle characteristic evaluation"
            )
        try:
            return LevelerBandsSpec.from_dict(
                {
                    "schema_version": descriptor["schema_version"],
                    "bands": descriptor["bands"],
                }
            )
        except LevelerBandError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        spec = self.validate(descriptor)
        if context.source_logical_object_id is None:
            raise SemanticNodeError(
                "Leveler bands require one logical source incarnation"
            )
        try:
            active = spec.active_band(context.source_counters)
        except LevelerBandError as exc:
            raise SemanticNodeError(str(exc)) from exc
        identity = ContinuousObjectIdentity(
            object_id=context.source_object_id,
            logical_object_id=context.source_logical_object_id,
        )
        inactive_bands = tuple(
            band for band in spec.bands if band is not active
        )
        ability_operations = [
            *(
                ContinuousOperation("add_ability", keyword)
                for keyword in (active.keywords if active is not None else ())
            ),
            *(
                ContinuousOperation(
                    "remove_ability_fragment",
                    ability_fragment_to_dict(
                        StaticComponentSpec(semantic_key)
                    ),
                )
                for band in inactive_bands
                for semantic_key in band.semantic_keys
            ),
        ]
        effects: list[ContinuousEffect] = []
        if ability_operations:
            effects.append(
                ContinuousEffect(
                    effect_id=(
                        f"{context.source_object_id}:"
                        f"{context.component_id}:level-abilities"
                    ),
                    source_id=context.source_object_id,
                    layer=Layer.ABILITY,
                    sublayer="6",
                    timestamp=context.source_timestamp,
                    operations=tuple(ability_operations),
                    origin=ContinuousEffectOrigin.STATIC_ABILITY,
                    applies=ObjectQuerySpec(zones=("battlefield",)),
                    relation=ContinuousEffectRelation.SOURCE_OBJECT,
                    related_object=identity,
                )
            )
        if active is not None:
            effects.append(
                ContinuousEffect(
                    effect_id=(
                        f"{context.source_object_id}:"
                        f"{context.component_id}:level-power-toughness"
                    ),
                    source_id=context.source_object_id,
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7b",
                    timestamp=context.source_timestamp,
                    operations=(
                        ContinuousOperation(
                            "set_power_toughness",
                            [active.power, active.toughness],
                        ),
                    ),
                    origin=ContinuousEffectOrigin.STATIC_ABILITY,
                    applies=ObjectQuerySpec(zones=("battlefield",)),
                    relation=ContinuousEffectRelation.SOURCE_OBJECT,
                    related_object=identity,
                )
            )
        return tuple(effects)


class ContinuousEffectComponentRegistry(
    RuntimeComponentRegistry[
        ContinuousEffectSourceContext,
        ContinuousEffect,
    ]
):
    def source_component_layer(self, descriptor: Mapping[str, Any]) -> Layer:
        """Read source ability participation at the layer declared by its owner."""
        handler = self._handlers.get(str(descriptor.get('handler_id') or ''))
        if handler is None:
            return Layer.ABILITY
        descriptor_layer=getattr(handler,'descriptor_source_component_layer',None)
        layer=descriptor_layer(descriptor) if descriptor_layer is not None else getattr(handler, 'source_component_layer', Layer.ABILITY)
        if not isinstance(layer, Layer):
            raise SemanticNodeError('Continuous source participation requires a typed layer')
        if layer < Layer.ABILITY:
            handler.validate(descriptor)
        return layer

    def may_affect_static_components(self, descriptor: Mapping[str, Any]) -> bool:
        """Only an explicit handler-owned exclusion can narrow a presence scan."""
        predicate = getattr(self._handler(descriptor), "may_affect_static_components", None)
        if predicate is None:
            return True
        return predicate(descriptor) is not False


@lru_cache(maxsize=1)
def default_continuous_effect_component_registry(
) -> ContinuousEffectComponentRegistry:
    from .conditional_flash import ConditionalFlashKeywordHandler
    from .negative_keyword_grants import DisjointNegativeKeywordGrantHandler
    from .enchanted_player_characteristics import EnchantedPlayerCharacteristicsHandler
    from .defender_declarations import DefenderDeclarationPermissionHandler
    from .conditional_color_grants import ConditionalColorGrantHandler
    from .keyword_prohibitions import OpponentKeywordProhibitionHandler
    from .basic_type_addition import ScopedBasicTypeAdditionHandler
    from .basic_type_setting import FixedBasicTypeSettingHandler
    from .chosen_characteristics import ChosenCharacteristicsHandler
    from .conditional_chosen_characteristics import ConditionalChosenCharacteristicsHandler
    from .attached_continuous import AttachedFixedCharacteristicsHandler
    from .chosen_aura_protection import SourceChosenAuraProtectionHandler
    from .soulbond import SoulbondPairedGrantHandler
    from .fixed_characteristic_settings import FixedCharacteristicSettingsHandler, ConditionalCharacteristicSettingsHandler, ConditionalCreatureTypeRemovalHandler
    from .attached_control import AttachedControlHandler
    from .quantified_anthems import QuantifiedAnthemHandler
    from .recipient_anthems import RecipientAnthemHandler
    from .shared_type_anthems import SharedTypeAnthemHandler
    from .graveyard_grants import GraveyardQueryAbilityGrantHandler, GraveyardConditionalAbilityGrantHandler
    from .composite_grants import CompositeAbilityGrantHandler, FixedTypeReplacementGrantHandler
    from .conditional_continuous import (
        FixedPublicStateCharacteristicsHandler,
        FixedPublicStateGrantedAbilityHandler,
        ConditionalDefenderPermissionHandler,
        ConditionalAsUnblockedAssignmentHandler,
        ConditionalBlockCapacityHandler,
        ConditionalDeclarationGrantHandler,
        GraveyardStaticCharacteristicsHandler,
        ConditionalTypedKeywordHandler,
    )

    registry = ContinuousEffectComponentRegistry(
        (
            OpponentKeywordProhibitionHandler(),
            ChosenCharacteristicsHandler(),
            ConditionalChosenCharacteristicsHandler(),
            FixedPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(
                handler_id="continuous.anthem.source-counter-query.v3", schema_version=3,
            ),
            AddBasicLandTypeHandler(),
            ScopedBasicTypeAdditionHandler(),
            FixedBasicTypeSettingHandler(),
            FixedQueryAbilityGrantHandler(),
            FixedQueryKeywordGrantHandler(),
            FixedQueryCharacteristicGrantHandler(),
            FixedQueryProtectionGrantHandler(),
            FixedQueryCharacteristicProtectionGrantHandler(),
            LevelerBandsHandler(),
            FixedPublicStateCharacteristicsHandler(),
            FixedPublicStateGrantedAbilityHandler(),
            ConditionalColorGrantHandler(),
            ConditionalFlashKeywordHandler(),
            FixedQueryPowerToughnessAnthemHandler(
                handler_id='continuous.anthem.source-counter-total-query.v4', schema_version=4,
                capability_dependencies=('continuous.power_toughness.fixed_anthem', COUNTER_TOTAL_CAPABILITY)),
            ConditionalDefenderPermissionHandler(),
            DefenderDeclarationPermissionHandler(),
            ConditionalAsUnblockedAssignmentHandler(),
            ConditionalBlockCapacityHandler(),
            ConditionalDeclarationGrantHandler(),
            GraveyardStaticCharacteristicsHandler(),
            GraveyardQueryAbilityGrantHandler(),
            CompositeAbilityGrantHandler(),
            FixedTypeReplacementGrantHandler(),
            GraveyardConditionalAbilityGrantHandler(),
            ConditionalTypedKeywordHandler(),
            AttachedFixedCharacteristicsHandler(),
            SourceChosenAuraProtectionHandler(),
            SoulbondPairedGrantHandler(),
            FixedCharacteristicSettingsHandler(),
            EnchantedPlayerCharacteristicsHandler(),
            DisjointNegativeKeywordGrantHandler(),
            ConditionalCharacteristicSettingsHandler(),
            ConditionalCreatureTypeRemovalHandler(),
            AttachedControlHandler(),
            QuantifiedAnthemHandler(),
            RecipientAnthemHandler(),
            SharedTypeAnthemHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()
