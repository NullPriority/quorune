from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..ability_fragments import (
    AllCreatureTypesCharacteristicDefinitionSpec,
    AbilityFragmentError,
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
    ColorlessCharacteristicDefinitionSpec,
    ConditionalKeywordSpec,
    CounterMaximumSpec,
    DeclarationCostTemplate,
    DeclarationRequirementTemplate,
    DeclarationRestrictionTemplate,
    DamageKeywordTriggerKind,
    DamageKeywordTriggerSpec,
    DynamicPowerToughnessSpec,
    QueryCharacteristicModifierSpec,
    QueryPowerToughnessDefinitionSpec,
    ProtectionSpec,
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
    StaticAbilityFragment,
    ToxicSpec,
    ability_fragment_from_dict,
)
from ..enchant_spec import SimpleEnchantSpec, TypedEnchantSpec
from ..enchant_spec import LinkedGraveyardCreatureEnchantSpec
from ..declaration_fragments import DECLARATION_COMPONENT_CAPABILITY_ID
from ..rules.capabilities import load_default_capability_registry
from ..trigger_participation import TriggerMultiplierSpec, WardSpec
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


ENCHANT_FRAGMENT_HANDLER_ID = "ability.static.enchant.v1"
TYPED_ENCHANT_FRAGMENT_HANDLER_ID = "ability.static.enchant.typed.v2"
LINKED_GRAVEYARD_ENCHANT_HANDLER_ID = (
    "ability.enchant.linked_graveyard_creature.v1"
)
PROTECTION_FRAGMENT_HANDLER_ID = "ability.static.protection.v1"
FLANKING_FRAGMENT_HANDLER_ID = "ability.trigger.flanking.v1"
BUSHIDO_FRAGMENT_HANDLER_ID = "ability.trigger.bushido.v1"
EXALTED_FRAGMENT_HANDLER_ID = "ability.trigger.exalted.v1"
BATTLE_CRY_FRAGMENT_HANDLER_ID = "ability.trigger.battle_cry.v1"
MELEE_FRAGMENT_HANDLER_ID = "ability.trigger.melee.v1"
MENTOR_FRAGMENT_HANDLER_ID = "ability.trigger.mentor.v1"
DETHRONE_FRAGMENT_HANDLER_ID = "ability.trigger.dethrone.v1"
TRAINING_FRAGMENT_HANDLER_ID = "ability.trigger.training.v1"
RENOWN_FRAGMENT_HANDLER_ID = "ability.trigger.renown.v1"
CASCADE_FRAGMENT_HANDLER_ID = "ability.trigger.cascade.v1"
PROWESS_FRAGMENT_HANDLER_ID = "ability.trigger.prowess.v1"
STORM_FRAGMENT_HANDLER_ID = "ability.trigger.storm.v1"
TRIGGER_MULTIPLIER_FRAGMENT_HANDLER_ID = (
    "ability.static.trigger-multiplier.v1"
)
WARD_FRAGMENT_HANDLER_ID = "ability.trigger.ward.v1"
TOXIC_FRAGMENT_HANDLER_ID = "ability.static.toxic.v1"
COUNTER_MAXIMUM_FRAGMENT_HANDLER_ID = "ability.static.counter-maximum.v1"
CONDITIONAL_KEYWORD_FRAGMENT_HANDLER_ID = (
    "ability.static.conditional-keyword.v1"
)
DYNAMIC_POWER_TOUGHNESS_FRAGMENT_HANDLER_ID = (
    "ability.static.dynamic-power-toughness.v1"
)
QUERY_CHARACTERISTIC_MODIFIER_FRAGMENT_HANDLER_ID = (
    "ability.static.query-characteristic-modifier.v1"
)
QUERY_POWER_TOUGHNESS_DEFINITION_FRAGMENT_HANDLER_ID = (
    "ability.static.query-power-toughness-definition.v1"
)
COLORLESS_CHARACTERISTIC_DEFINITION_FRAGMENT_HANDLER_ID = (
    "ability.static.colorless-characteristic-definition.v1"
)
ALL_CREATURE_TYPES_CHARACTERISTIC_DEFINITION_FRAGMENT_HANDLER_ID = (
    "ability.static.all-creature-types-characteristic-definition.v1"
)
DECLARATION_COST_FRAGMENT_HANDLER_ID = (
    "ability.static.declaration-cost.v1"
)
DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID = (
    "ability.static.declaration-requirement.v1"
)
DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID = (
    "ability.static.declaration-restriction.v1"
)


def _fragment(
    descriptor: Mapping[str, Any],
    *,
    handler_id: str,
    event: str,
    expected_type: type[StaticAbilityFragment],
) -> StaticAbilityFragment:
    exact_fields(
        descriptor,
        {"handler_id", "schema_version", "event", "fragment"},
        field="static ability fragment handler",
    )
    if descriptor["handler_id"] != handler_id:
        raise SemanticNodeError("Static ability fragment handler ID mismatch")
    if (
        type(descriptor["schema_version"]) is not int
        or descriptor["schema_version"] != 1
    ):
        raise SemanticNodeError(
            f"Unsupported {handler_id} schema version"
        )
    if descriptor["event"] != event:
        raise SemanticNodeError(
            f"{handler_id} must use the {event} event"
        )
    if not isinstance(descriptor["fragment"], Mapping):
        raise SemanticNodeError(
            "Static ability fragment must be an object"
        )
    try:
        fragment = ability_fragment_from_dict(descriptor["fragment"])
    except AbilityFragmentError as exc:
        raise SemanticNodeError(str(exc)) from exc
    if not isinstance(fragment, expected_type):
        raise SemanticNodeError(
            f"{handler_id} carries the wrong typed fragment"
        )
    return fragment


@dataclass(frozen=True, slots=True)
class EnchantAbilityFragmentHandler:
    handler_id: str = ENCHANT_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.enchant"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("303.4", "702.5a")
    capability_dependencies: tuple[str, ...] = (
        "attachment.aura.simple_object",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> SimpleEnchantSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=SimpleEnchantSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class TypedEnchantAbilityFragmentHandler:
    handler_id: str = TYPED_ENCHANT_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.enchant.typed"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("303.4", "702.5a")
    capability_dependencies: tuple[str, ...] = (
        "attachment.aura.typed_restriction",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> TypedEnchantSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=TypedEnchantSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class ProtectionAbilityFragmentHandler:
    handler_id: str = PROTECTION_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.protection"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.16", "702.16a")
    capability_dependencies: tuple[str, ...] = (
        "protection.typed.debt",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> ProtectionSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=ProtectionSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class DeclarationCostAbilityFragmentHandler:
    handler_id: str = DECLARATION_COST_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.declaration_cost"
    event: str = "combat.declaration"
    rule_references: tuple[str, ...] = ("508.1h", "509.1d")
    capability_dependencies: tuple[str, ...] = (
        DECLARATION_COMPONENT_CAPABILITY_ID,
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DeclarationCostTemplate:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=DeclarationCostTemplate,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class DeclarationRequirementAbilityFragmentHandler:
    handler_id: str = DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.declaration_requirement"
    event: str = "combat.declaration"
    rule_references: tuple[str, ...] = ("508.1d", "509.1c")
    capability_dependencies: tuple[str, ...] = (
        DECLARATION_COMPONENT_CAPABILITY_ID,
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DeclarationRequirementTemplate:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=DeclarationRequirementTemplate,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class DeclarationRestrictionAbilityFragmentHandler:
    handler_id: str = DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.declaration_restriction"
    event: str = "combat.declaration"
    rule_references: tuple[str, ...] = ("508.1c", "509.1b")
    capability_dependencies: tuple[str, ...] = (
        DECLARATION_COMPONENT_CAPABILITY_ID,
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DeclarationRestrictionTemplate:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=DeclarationRestrictionTemplate,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class LinkedGraveyardEnchantFragmentHandler:
    handler_id: str = LINKED_GRAVEYARD_ENCHANT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.enchant.linked_graveyard_creature"
    event: str = "resolve"
    rule_references: tuple[str, ...] = (
        "303.4",
        "303.4a",
        "303.4f",
        "702.5a",
    )
    capability_dependencies: tuple[str, ...] = ()

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> LinkedGraveyardCreatureEnchantSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=LinkedGraveyardCreatureEnchantSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class FlankingAbilityFragmentHandler:
    handler_id: str = FLANKING_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.flanking"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.25", "702.25a", "702.25b")
    capability_dependencies: tuple[str, ...] = (
        "combat.trigger.flanking",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.FLANKING:
            raise SemanticNodeError(
                "The Flanking runtime handler requires a Flanking fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class BushidoAbilityFragmentHandler:
    handler_id: str = BUSHIDO_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.bushido"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.45", "702.45a", "702.45b")
    capability_dependencies: tuple[str, ...] = (
        "combat.trigger.bushido",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.BUSHIDO:
            raise SemanticNodeError(
                "The Bushido runtime handler requires a Bushido fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class ExaltedAbilityFragmentHandler:
    handler_id: str = EXALTED_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.exalted"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.83", "702.83a", "702.83b")
    capability_dependencies: tuple[str, ...] = ("combat.trigger.exalted",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.EXALTED:
            raise SemanticNodeError(
                "The Exalted runtime handler requires an Exalted fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class BattleCryAbilityFragmentHandler:
    handler_id: str = BATTLE_CRY_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.battle_cry"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.91", "702.91a", "702.91b")
    capability_dependencies: tuple[str, ...] = ("combat.trigger.battle_cry",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.BATTLE_CRY:
            raise SemanticNodeError(
                "The Battle Cry runtime handler requires a Battle Cry fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class MeleeAbilityFragmentHandler:
    handler_id: str = MELEE_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.melee"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.121", "702.121a", "702.121b")
    capability_dependencies: tuple[str, ...] = ("combat.trigger.melee",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.MELEE:
            raise SemanticNodeError(
                "The Melee runtime handler requires a Melee fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class MentorAbilityFragmentHandler:
    handler_id: str = MENTOR_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.mentor"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.134", "702.134a", "702.134b")
    capability_dependencies: tuple[str, ...] = ("counter.producer.mentor",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.MENTOR:
            raise SemanticNodeError(
                "The Mentor runtime handler requires a Mentor fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class DethroneAbilityFragmentHandler:
    handler_id: str = DETHRONE_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.dethrone"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.105", "702.105a", "702.105b")
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.dethrone",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.DETHRONE:
            raise SemanticNodeError(
                "The Dethrone runtime handler requires a Dethrone fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class TrainingAbilityFragmentHandler:
    handler_id: str = TRAINING_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.training"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("702.149", "702.149a", "702.149b")
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.training",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> CombatKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CombatKeywordTriggerSpec,
        )
        if fragment.kind is not CombatKeywordTriggerKind.TRAINING:
            raise SemanticNodeError(
                "The Training runtime handler requires a Training fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class RenownAbilityFragmentHandler:
    handler_id: str = RENOWN_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.renown"
    event: str = "damage.dealt.self"
    rule_references: tuple[str, ...] = (
        "702.112",
        "702.112a",
        "702.112b",
        "702.112c",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.renown",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DamageKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=DamageKeywordTriggerSpec,
        )
        if fragment.kind is not DamageKeywordTriggerKind.RENOWN:
            raise SemanticNodeError(
                "The Renown runtime handler requires a Renown fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class CascadeAbilityFragmentHandler:
    handler_id: str = CASCADE_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.cascade"
    event: str = "spell.cast"
    rule_references: tuple[str, ...] = (
        "601.2i",
        "603.2",
        "603.3",
        "702.85",
        "702.85a",
        "702.85c",
    )
    capability_dependencies: tuple[str, ...] = ("trigger.keyword.cascade",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> SpellCastKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=SpellCastKeywordTriggerSpec,
        )
        if fragment.kind is not SpellCastKeywordTriggerKind.CASCADE:
            raise SemanticNodeError(
                "The Cascade runtime handler requires a Cascade fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class StormAbilityFragmentHandler:
    handler_id: str = STORM_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.storm"
    event: str = "spell.cast"
    rule_references: tuple[str, ...] = (
        "601.2i",
        "603.2",
        "603.3",
        "702.40",
        "702.40a",
        "702.40b",
    )
    capability_dependencies: tuple[str, ...] = ("trigger.keyword.storm",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> SpellCastKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=SpellCastKeywordTriggerSpec,
        )
        if fragment.kind is not SpellCastKeywordTriggerKind.STORM:
            raise SemanticNodeError(
                "The Storm runtime handler requires a Storm fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class ProwessAbilityFragmentHandler:
    handler_id: str = PROWESS_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.prowess"
    event: str = "spell.cast"
    rule_references: tuple[str, ...] = (
        "601.2i",
        "603.2",
        "603.3",
        "702.108",
        "702.108a",
        "702.108b",
    )
    capability_dependencies: tuple[str, ...] = ("trigger.keyword.prowess",)

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> SpellCastKeywordTriggerSpec:
        fragment = _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=SpellCastKeywordTriggerSpec,
        )
        if fragment.kind is not SpellCastKeywordTriggerKind.PROWESS:
            raise SemanticNodeError(
                "The Prowess runtime handler requires a Prowess fragment"
            )
        return fragment

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class TriggerMultiplierAbilityFragmentHandler:
    handler_id: str = TRIGGER_MULTIPLIER_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.trigger_multiplier"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("603.2d",)
    capability_dependencies: tuple[str, ...] = (
        "trigger.multiplier.artifact_or_creature_enters",
        "trigger.multiplier.another_creature_of_chosen_type",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> TriggerMultiplierSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=TriggerMultiplierSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class WardAbilityFragmentHandler:
    handler_id: str = WARD_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.trigger.ward"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("603.3", "702.21", "702.21a")
    capability_dependencies: tuple[str, ...] = (
        "trigger.keyword.ward.fixed_generic",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> WardSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=WardSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class ToxicAbilityFragmentHandler:
    handler_id: str = TOXIC_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.toxic"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("120.3g", "702.164", "702.164a")
    capability_dependencies: tuple[str, ...] = ("damage.result.toxic",)

    def validate(self, descriptor: Mapping[str, Any]) -> ToxicSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=ToxicSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class CounterMaximumAbilityFragmentHandler:
    handler_id: str = COUNTER_MAXIMUM_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.counter_maximum"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("704.3", "704.5r")
    capability_dependencies: tuple[str, ...] = (
        "state_based.counter_maximum.fixed_self",
    )

    def validate(self, descriptor: Mapping[str, Any]) -> CounterMaximumSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=CounterMaximumSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class ConditionalKeywordAbilityFragmentHandler:
    handler_id: str = CONDITIONAL_KEYWORD_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.conditional_keyword"
    event: str = "continuous"
    rule_references: tuple[str, ...] = ("604.1", "611.3a", "613.1f")
    capability_dependencies: tuple[str, ...] = (
        "continuous.characteristics.conditional_keyword",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> ConditionalKeywordSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=ConditionalKeywordSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class DynamicPowerToughnessAbilityFragmentHandler:
    handler_id: str = DYNAMIC_POWER_TOUGHNESS_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.dynamic_power_toughness"
    event: str = "continuous"
    rule_references: tuple[str, ...] = (
        "604.1",
        "611.3a",
        "613.1g",
        "613.4b",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.characteristics.dynamic_power_toughness",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> DynamicPowerToughnessSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=DynamicPowerToughnessSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class QueryCharacteristicModifierAbilityFragmentHandler:
    handler_id: str = QUERY_CHARACTERISTIC_MODIFIER_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.query_characteristic_modifier"
    event: str = "continuous"
    rule_references: tuple[str, ...] = (
        "604.1",
        "611.3a",
        "613.1f",
        "613.1g",
        "613.4b",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.characteristics.query_count_modifier",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> QueryCharacteristicModifierSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=QueryCharacteristicModifierSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class QueryPowerToughnessDefinitionAbilityFragmentHandler:
    handler_id: str = QUERY_POWER_TOUGHNESS_DEFINITION_FRAGMENT_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.static.query_power_toughness_definition"
    event: str = "continuous"
    rule_references: tuple[str, ...] = (
        "604.3",
        "611.3a",
        "613.1g",
        "613.4a",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.characteristics.query_power_toughness_definition",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> QueryPowerToughnessDefinitionSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=QueryPowerToughnessDefinitionSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class AllCreatureTypesCharacteristicDefinitionAbilityFragmentHandler:
    handler_id: str = (
        ALL_CREATURE_TYPES_CHARACTERISTIC_DEFINITION_FRAGMENT_HANDLER_ID
    )
    schema_version: int = 1
    family: str = "ability.static.all_creature_types_characteristic_definition"
    event: str = "continuous"
    rule_references: tuple[str, ...] = (
        "205.3m",
        "604.3",
        "613.1d",
        "702.73",
        "702.73a",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.characteristics.changeling",
    )

    def validate(
        self,
        descriptor: Mapping[str, Any],
    ) -> AllCreatureTypesCharacteristicDefinitionSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=AllCreatureTypesCharacteristicDefinitionSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class ColorlessCharacteristicDefinitionAbilityFragmentHandler:
    handler_id: str = (
        COLORLESS_CHARACTERISTIC_DEFINITION_FRAGMENT_HANDLER_ID
    )
    schema_version: int = 1
    family: str = "ability.static.colorless_characteristic_definition"
    event: str = "continuous"
    rule_references: tuple[str, ...] = (
        "604.3",
        "613.1e",
        "702.114",
        "702.114a",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.characteristics.devoid",
    )

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> ColorlessCharacteristicDefinitionSpec:
        return _fragment(
            descriptor,
            handler_id=self.handler_id,
            event=self.event,
            expected_type=ColorlessCharacteristicDefinitionSpec,
        )

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[StaticAbilityFragment, ...]:
        del context
        return (self.validate(descriptor),)


class AbilityFragmentRegistry(
    RuntimeComponentRegistry[object, StaticAbilityFragment]
):
    pass


@lru_cache(maxsize=1)
def default_ability_fragment_registry() -> AbilityFragmentRegistry:
    registry = AbilityFragmentRegistry(
        (
            AllCreatureTypesCharacteristicDefinitionAbilityFragmentHandler(),
            BattleCryAbilityFragmentHandler(),
            BushidoAbilityFragmentHandler(),
            CascadeAbilityFragmentHandler(),
            CounterMaximumAbilityFragmentHandler(),
            DeclarationCostAbilityFragmentHandler(),
            DeclarationRequirementAbilityFragmentHandler(),
            DeclarationRestrictionAbilityFragmentHandler(),
            ConditionalKeywordAbilityFragmentHandler(),
            ColorlessCharacteristicDefinitionAbilityFragmentHandler(),
            DethroneAbilityFragmentHandler(),
            DynamicPowerToughnessAbilityFragmentHandler(),
            QueryCharacteristicModifierAbilityFragmentHandler(),
            QueryPowerToughnessDefinitionAbilityFragmentHandler(),
            EnchantAbilityFragmentHandler(),
            TypedEnchantAbilityFragmentHandler(),
            ExaltedAbilityFragmentHandler(),
            FlankingAbilityFragmentHandler(),
            LinkedGraveyardEnchantFragmentHandler(),
            MeleeAbilityFragmentHandler(),
            MentorAbilityFragmentHandler(),
            ProtectionAbilityFragmentHandler(),
            ProwessAbilityFragmentHandler(),
            RenownAbilityFragmentHandler(),
            StormAbilityFragmentHandler(),
            TrainingAbilityFragmentHandler(),
            TriggerMultiplierAbilityFragmentHandler(),
            ToxicAbilityFragmentHandler(),
            WardAbilityFragmentHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()


def fragments_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[StaticAbilityFragment, ...]:
    registry = default_ability_fragment_registry()
    fragments: list[StaticAbilityFragment] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        fragments.extend(registry.lower(descriptor, None))
    return tuple(fragments)


__all__ = [
    "ALL_CREATURE_TYPES_CHARACTERISTIC_DEFINITION_FRAGMENT_HANDLER_ID",
    "ENCHANT_FRAGMENT_HANDLER_ID",
    "TYPED_ENCHANT_FRAGMENT_HANDLER_ID",
    "BUSHIDO_FRAGMENT_HANDLER_ID",
    "BATTLE_CRY_FRAGMENT_HANDLER_ID",
    "CASCADE_FRAGMENT_HANDLER_ID",
    "COUNTER_MAXIMUM_FRAGMENT_HANDLER_ID",
    "CONDITIONAL_KEYWORD_FRAGMENT_HANDLER_ID",
    "COLORLESS_CHARACTERISTIC_DEFINITION_FRAGMENT_HANDLER_ID",
    "DECLARATION_COST_FRAGMENT_HANDLER_ID",
    "DECLARATION_REQUIREMENT_FRAGMENT_HANDLER_ID",
    "DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID",
    "EXALTED_FRAGMENT_HANDLER_ID",
    "DYNAMIC_POWER_TOUGHNESS_FRAGMENT_HANDLER_ID",
    "QUERY_CHARACTERISTIC_MODIFIER_FRAGMENT_HANDLER_ID",
    "QUERY_POWER_TOUGHNESS_DEFINITION_FRAGMENT_HANDLER_ID",
    "FLANKING_FRAGMENT_HANDLER_ID",
    "LINKED_GRAVEYARD_ENCHANT_HANDLER_ID",
    "PROTECTION_FRAGMENT_HANDLER_ID",
    "MELEE_FRAGMENT_HANDLER_ID",
    "MENTOR_FRAGMENT_HANDLER_ID",
    "DETHRONE_FRAGMENT_HANDLER_ID",
    "TRAINING_FRAGMENT_HANDLER_ID",
    "RENOWN_FRAGMENT_HANDLER_ID",
    "PROWESS_FRAGMENT_HANDLER_ID",
    "STORM_FRAGMENT_HANDLER_ID",
    "TRIGGER_MULTIPLIER_FRAGMENT_HANDLER_ID",
    "WARD_FRAGMENT_HANDLER_ID",
    "TOXIC_FRAGMENT_HANDLER_ID",
    "EnchantAbilityFragmentHandler",
    "TypedEnchantAbilityFragmentHandler",
    "BushidoAbilityFragmentHandler",
    "BattleCryAbilityFragmentHandler",
    "CascadeAbilityFragmentHandler",
    "CounterMaximumAbilityFragmentHandler",
    "DeclarationCostAbilityFragmentHandler",
    "DeclarationRequirementAbilityFragmentHandler",
    "DeclarationRestrictionAbilityFragmentHandler",
    "ConditionalKeywordAbilityFragmentHandler",
    "AllCreatureTypesCharacteristicDefinitionAbilityFragmentHandler",
    "ColorlessCharacteristicDefinitionAbilityFragmentHandler",
    "ExaltedAbilityFragmentHandler",
    "FlankingAbilityFragmentHandler",
    "LinkedGraveyardEnchantFragmentHandler",
    "MeleeAbilityFragmentHandler",
    "MentorAbilityFragmentHandler",
    "DethroneAbilityFragmentHandler",
    "DynamicPowerToughnessAbilityFragmentHandler",
    "QueryCharacteristicModifierAbilityFragmentHandler",
    "QueryPowerToughnessDefinitionAbilityFragmentHandler",
    "TrainingAbilityFragmentHandler",
    "RenownAbilityFragmentHandler",
    "ProwessAbilityFragmentHandler",
    "StormAbilityFragmentHandler",
    "TriggerMultiplierAbilityFragmentHandler",
    "ToxicAbilityFragmentHandler",
    "WardAbilityFragmentHandler",
    "ProtectionAbilityFragmentHandler",
    "AbilityFragmentRegistry",
    "default_ability_fragment_registry",
    "fragments_from_descriptors",
]
