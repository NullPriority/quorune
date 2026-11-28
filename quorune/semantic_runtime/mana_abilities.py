from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..artifact_hand_mana_model import (
    ARTIFACT_HAND_MANA_CAPABILITY, ARTIFACT_HAND_MANA_HANDLER_ID, ARTIFACT_HAND_MANA_RULES,
    ArtifactHandActivatedManaSpec,
)
from ..fixed_mana_results import DISCARD_HAND_MANA_SOURCE, EXERT_MANA_CAPABILITY, EXERT_MANA_SOURCE
from ..rules.exert_mana import EXERT_MANA_HANDLER_ID
from ..rules.forage_mana import CAPABILITY as FORAGE_MANA_CAPABILITY, HANDLER_ID as FORAGE_MANA_HANDLER_ID, has_forage_mana_choice
from ..rules.forage import FORAGE_ACTIVATION_CAPABILITY
from ..rules.whole_hand_mana import CAPABILITY as WHOLE_HAND_MANA_CAPABILITY, HANDLER_ID as WHOLE_HAND_MANA_HANDLER_ID
from ..fixed_mana_abilities import (
    FIXED_MANA_HANDLER_CAPABILITIES,
    FIXED_MANA_HANDLER_ID,
    HAND_EXILE_MANA_CAPABILITY,
    HAND_EXILE_MANA_HANDLER_ID,
    HAND_EXILE_MANA_RULES,
    FIXED_MANA_RESULT_HANDLER_ID,
    FIXED_MANA_SELECTED_COST_HANDLER_ID,
    FIXED_MANA_SPEND_RULE_HANDLER_ID,
    LUCK_COUNTER_MANA_CAPABILITY,
    LUCK_COUNTER_MANA_HANDLER_ID,
    FixedActivatedManaAbilitySpec,
    FixedManaAbilityError,
)
from ..land_mana_potential import (
    LAND_MANA_POTENTIAL_CAPABILITY, LAND_MANA_POTENTIAL_HANDLER_ID,
    LAND_MANA_POTENTIAL_RULES, LandPotentialActivatedManaSpec,
)
from ..land_play_mana_model import (
    LAND_PLAY_MANA_CAPABILITY, LAND_PLAY_MANA_HANDLER_ID, LAND_PLAY_MANA_RULES, LandPlayActivatedManaSpec,
)
from ..mana_spend_rules import MANA_SPEND_RULE_CAPABILITY, MANA_SPEND_RULES, decode_mana_spend_rule
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError
from .public_quantity_mana import PublicQuantityActivatedManaHandler
from .chosen_color_mana import ChosenColorActivatedManaHandler


@dataclass(frozen=True, slots=True)
class FixedActivatedManaAbilityHandler:
    handler_id: str = FIXED_MANA_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.mana.fixed-output"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "605.1a",
        "605.2",
        "605.3a",
        "605.3b",
    )
    capability_dependencies: tuple[str, ...] = FIXED_MANA_HANDLER_CAPABILITIES

    def validate(
        self, descriptor: Mapping[str, Any]
    ) -> FixedActivatedManaAbilitySpec:
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "ability"},
            field="fixed activated mana handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError("Fixed activated mana handler ID mismatch")
        if type(descriptor['schema_version']) is not int or descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported fixed activated mana handler schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Fixed activated mana handler must use the activate event"
            )
        ability = descriptor["ability"]
        if not isinstance(ability, Mapping):
            raise SemanticNodeError("Fixed activated mana ability must be an object")
        try:
            spec=FixedActivatedManaAbilitySpec.from_dict(ability)
            exert = spec.result is not None and spec.result.kind == EXERT_MANA_SOURCE
            if exert != (self.schema_version == 7):
                raise FixedManaAbilityError("Exert mana requires schema version seven")
            whole_hand = spec.result is not None and spec.result.kind == DISCARD_HAND_MANA_SOURCE
            if whole_hand != (self.schema_version == 9):
                raise FixedManaAbilityError("Whole-hand mana requires its registered version")
            if (spec.result is not None and not (exert or whole_hand)) != (self.schema_version == 2):
                raise FixedManaAbilityError('Fixed mana result requires schema version two')
            if bool(spec.choices) != (self.schema_version in {3, 8}):
                raise FixedManaAbilityError("Selected mana costs require their registered version")
            if has_forage_mana_choice(spec) != (self.schema_version == 8):
                raise FixedManaAbilityError("Forage mana requires its distinct typed handler")
            if (decode_mana_spend_rule(spec.spend_restriction) is not None) != (self.schema_version in {4, 7}):
                raise FixedManaAbilityError("Typed spending rules require their registered version")
            if spec.luck_counter_color_choice != (self.schema_version == 5):
                raise FixedManaAbilityError("Luck-counter color choice requires schema version five")
            if spec.exile_source_from_hand != (self.schema_version == 6):
                raise FixedManaAbilityError("Hand-source exile requires schema version six")
            return spec
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[FixedActivatedManaAbilitySpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedManaAbilityRegistry(
    RuntimeComponentRegistry[object, Any]
):
    pass


@dataclass(frozen=True,slots=True)
class FixedManaResultAbilityHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = FIXED_MANA_RESULT_HANDLER_ID
    schema_version: int = 2
    capability_dependencies: tuple[str,...] = (*FIXED_MANA_HANDLER_CAPABILITIES,'mana.activated.fixed_result')
    rule_references: tuple[str,...] = ('605.1a','605.3b','608.2c','120.3a','502.3')


@dataclass(frozen=True,slots=True)
class FixedManaSelectedCostAbilityHandler(FixedActivatedManaAbilityHandler):
    handler_id: str=FIXED_MANA_SELECTED_COST_HANDLER_ID
    schema_version: int=3
    capability_dependencies: tuple[str,...]=(*FIXED_MANA_HANDLER_CAPABILITIES,'activation.selected_zone_change.fixed','activation.selected_tap.fixed')
    rule_references: tuple[str,...]=('302.6','602.2b','605.1a','605.3b','701.21a')


@dataclass(frozen=True, slots=True)
class FixedManaSpendRuleAbilityHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = FIXED_MANA_SPEND_RULE_HANDLER_ID
    schema_version: int = 4
    capability_dependencies: tuple[str, ...] = (*FIXED_MANA_HANDLER_CAPABILITIES, MANA_SPEND_RULE_CAPABILITY)
    rule_references: tuple[str, ...] = MANA_SPEND_RULES


@dataclass(frozen=True, slots=True)
class LuckCounterColorManaHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = LUCK_COUNTER_MANA_HANDLER_ID
    schema_version: int = 5
    family: str = "ability.activated.mana.luck-counter-color"
    capability_dependencies: tuple[str, ...] = (*FIXED_MANA_HANDLER_CAPABILITIES, LUCK_COUNTER_MANA_CAPABILITY)
    rule_references: tuple[str, ...] = ("106.1", "122.1", "605.1a", "605.3b")


@dataclass(frozen=True, slots=True)
class HandExileActivatedManaHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = HAND_EXILE_MANA_HANDLER_ID
    schema_version: int = 6
    capability_dependencies: tuple[str, ...] = (*FIXED_MANA_HANDLER_CAPABILITIES, HAND_EXILE_MANA_CAPABILITY)
    rule_references: tuple[str, ...] = HAND_EXILE_MANA_RULES


@dataclass(frozen=True, slots=True)
class ExertActivatedManaHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = EXERT_MANA_HANDLER_ID
    schema_version: int = 7
    family: str = "ability.activated.mana.exert-source"
    capability_dependencies: tuple[str, ...] = (
        *FIXED_MANA_HANDLER_CAPABILITIES, MANA_SPEND_RULE_CAPABILITY, EXERT_MANA_CAPABILITY,
        "untap.step.exert_player",
    )
    rule_references: tuple[str, ...] = ("106.6", "602.2b", "605.3b", "701.43a")


@dataclass(frozen=True, slots=True)
class ForageActivatedManaHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = FORAGE_MANA_HANDLER_ID
    schema_version: int = 8
    family: str = "ability.activated.mana.forage-combination"
    capability_dependencies: tuple[str, ...] = (
        *FIXED_MANA_HANDLER_CAPABILITIES, FORAGE_ACTIVATION_CAPABILITY, FORAGE_MANA_CAPABILITY,
    )
    rule_references: tuple[str, ...] = ("106.1a", "602.2b", "605.3b", "701.61a")


@dataclass(frozen=True, slots=True)
class ArtifactHandActivatedManaHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = ARTIFACT_HAND_MANA_HANDLER_ID
    family: str = "ability.activated.mana.artifact-hand-reveal"
    rule_references: tuple[str, ...] = ARTIFACT_HAND_MANA_RULES
    capability_dependencies: tuple[str, ...] = (ARTIFACT_HAND_MANA_CAPABILITY,)

    def validate(self, descriptor):
        try:
            return ArtifactHandActivatedManaSpec(FixedActivatedManaAbilityHandler.validate(self, descriptor))
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class LandPotentialManaHandler:
    handler_id: str = LAND_MANA_POTENTIAL_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.mana.land-potential"
    event: str = "activate"
    rule_references: tuple[str, ...] = LAND_MANA_POTENTIAL_RULES
    capability_dependencies: tuple[str, ...] = (LAND_MANA_POTENTIAL_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "ability"},
                     field="land potential mana handler")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event):
            raise SemanticNodeError("Land potential mana handler identity changed")
        try:
            return LandPotentialActivatedManaSpec.from_dict(descriptor["ability"])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class WholeHandActivatedManaHandler(FixedActivatedManaAbilityHandler):
    handler_id: str = WHOLE_HAND_MANA_HANDLER_ID
    schema_version: int = 9
    family: str = "ability.activated.mana.whole-hand-sacrifice"
    capability_dependencies: tuple[str, ...] = (
        *FIXED_MANA_HANDLER_CAPABILITIES, WHOLE_HAND_MANA_CAPABILITY,
        "zone.discard.whole_hand", "zone.change.destination_replacement",
    )
    rule_references: tuple[str, ...] = ("304.5", "602.2b", "605.1a", "605.3b", "701.8a")


@dataclass(frozen=True, slots=True)
class LandPlayActivatedManaHandler:
    handler_id: str = LAND_PLAY_MANA_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.mana.land-play-output"
    event: str = "activate"
    rule_references: tuple[str, ...] = LAND_PLAY_MANA_RULES
    capability_dependencies: tuple[str, ...] = (LAND_PLAY_MANA_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> LandPlayActivatedManaSpec:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "ability"}, field="land-play mana handler")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event):
            raise SemanticNodeError("Land-play mana handler identity changed")
        try:
            return LandPlayActivatedManaSpec.from_dict(descriptor["ability"])
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


@lru_cache(maxsize=1)
def default_fixed_mana_ability_registry() -> FixedManaAbilityRegistry:
    registry = FixedManaAbilityRegistry((
        FixedActivatedManaAbilityHandler(),
        FixedManaResultAbilityHandler(),
        FixedManaSelectedCostAbilityHandler(),
        FixedManaSpendRuleAbilityHandler(),
        LuckCounterColorManaHandler(),
        HandExileActivatedManaHandler(),
        ExertActivatedManaHandler(),
        ForageActivatedManaHandler(),
        PublicQuantityActivatedManaHandler(),
        ChosenColorActivatedManaHandler(),
        LandPlayActivatedManaHandler(),
        ArtifactHandActivatedManaHandler(),
        WholeHandActivatedManaHandler(),
        LandPotentialManaHandler(),
    ))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def fixed_mana_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[Any, ...]:
    registry = default_fixed_mana_ability_registry()
    result: list[Any] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


def fixed_mana_life_runtime_dependencies(program):
    """Require the exact result in both the fixed handler and runtime catalog."""
    from dataclasses import replace

    from ..abilities import ActivatedAbility
    from ..compiler.activated_costs import activated_ability_cost
    from ..fixed_mana_results import FIXED_MANA_LIFE_GAIN_CAPABILITY, GAIN_CONTROLLER_LIFE
    from ..replacement.immutable import thaw_value
    from .activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    def is_life_result(value):
        return isinstance(value, Mapping) and (
            value.get("schema_version") == 3 or value.get("kind") == GAIN_CONTROLLER_LIFE
        )

    catalogs = tuple(row["ability"] for row in program.handlers
                     if row.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID)
    results = tuple(row for row in program.handlers if row.get("handler_id") == FIXED_MANA_RESULT_HANDLER_ID)
    if not (any(is_life_result(row.get("fixed_mana_result")) for row in catalogs)
            or any(is_life_result(row["ability"].get("result")) for row in results)):
        return ()
    if (len(catalogs) != 1 or len(results) != 1 or len(program.handlers) != 2
            or program.event != "activate" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.event_condition is not None or program.effects):
        raise ValueError("Printed fixed mana life gain requires the paired result and catalog")
    spec, = fixed_mana_specs_from_descriptors(results)
    expected = replace(spec.to_activated_ability(), builtin_semantic_key=program.key)
    if (expected.fixed_mana_result is None or expected.fixed_mana_result.kind != GAIN_CONTROLLER_LIFE
            or ActivatedAbility.from_dict(thaw_value(catalogs[0])) != expected
            or thaw_value(program.cost_schema) != activated_ability_cost(expected)):
        raise ValueError("Printed fixed mana life gain changed the result or its tap price")
    return (FIXED_MANA_LIFE_GAIN_CAPABILITY,)


def exert_mana_runtime_dependencies(program):
    """Require the exact exert cost, output and rider in both runtime carriers."""
    from dataclasses import replace

    from ..abilities import ActivatedAbility
    from ..compiler.activated_costs import activated_ability_cost
    from ..replacement.immutable import thaw_value
    from .activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    def has_exert(value):
        return isinstance(value, Mapping) and (value.get("schema_version") == 5 or value.get("kind") == EXERT_MANA_SOURCE)

    catalogs = tuple(row["ability"] for row in program.handlers
                     if row.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID)
    handlers = tuple(row for row in program.handlers if row.get("handler_id") == EXERT_MANA_HANDLER_ID)
    if not handlers and not any(has_exert(row.get("fixed_mana_result")) for row in catalogs):
        return whole_hand_mana_runtime_dependencies(program, catalogs)
    if (len(catalogs) != 1 or len(handlers) != 1 or len(program.handlers) != 2
            or program.event != "activate" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.event_condition is not None or program.effects):
        raise ValueError("Exert mana requires the paired exact mana and catalog descriptors")
    spec, = fixed_mana_specs_from_descriptors(handlers)
    expected = replace(spec.to_activated_ability(), builtin_semantic_key=program.key)
    if (ActivatedAbility.from_dict(thaw_value(catalogs[0])) != expected
            or thaw_value(program.cost_schema) != activated_ability_cost(expected)):
        raise ValueError("Exert mana activation changed its source price or spend rider")
    return (*spec.result.capability_dependencies, *decode_mana_spend_rule(spec.spend_restriction).capability_dependencies)


def next_cast_library_mana_runtime_dependencies(program):
    """Keep the paid mana result and its callable catalog in agreement."""
    from dataclasses import replace

    from ..abilities import ActivatedAbility
    from ..compiler.activated_costs import activated_ability_cost
    from ..replacement.immutable import thaw_value
    from ..rules.next_cast_library_access import (
        NEXT_CAST_LIBRARY_ACCESS, NEXT_CAST_LIBRARY_MANA_CAPABILITY, NEXT_CAST_LIBRARY_MANA_RESULT_VERSION,
    )
    from .activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    def denotes_next_cast(value):
        return isinstance(value, Mapping) and (
            value.get("schema_version") == NEXT_CAST_LIBRARY_MANA_RESULT_VERSION
            or value.get("kind") == NEXT_CAST_LIBRARY_ACCESS
        )

    catalogs = tuple(row["ability"] for row in program.handlers
                     if row.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID)
    results = tuple(row for row in program.handlers if row.get("handler_id") == FIXED_MANA_RESULT_HANDLER_ID)
    if not (any(denotes_next_cast(row.get("fixed_mana_result")) for row in catalogs)
            or any(denotes_next_cast(row["ability"].get("result")) for row in results)):
        return ()
    if (len(catalogs) != 1 or len(results) != 1 or len(program.handlers) != 2
            or program.event != "activate" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.event_condition is not None or program.effects):
        raise ValueError("Next-cast mana requires its paired fixed result and activation catalog")
    spec, = fixed_mana_specs_from_descriptors(results)
    expected = replace(spec.to_activated_ability(), builtin_semantic_key=program.key)
    if (spec.result is None or spec.result.kind != NEXT_CAST_LIBRARY_ACCESS
            or ActivatedAbility.from_dict(thaw_value(catalogs[0])) != expected
            or thaw_value(program.cost_schema) != activated_ability_cost(expected)):
        raise ValueError("Next-cast mana changed its printed price, mana or delayed result")
    return (NEXT_CAST_LIBRARY_MANA_CAPABILITY,)


def whole_hand_mana_runtime_dependencies(program, catalogs):
    from dataclasses import replace

    from ..abilities import ActivatedAbility
    from ..compiler.activated_costs import activated_ability_cost
    from ..replacement.immutable import thaw_value

    handlers = tuple(row for row in program.handlers if row.get("handler_id") == WHOLE_HAND_MANA_HANDLER_ID)
    def has_whole_hand(value):
        return isinstance(value, Mapping) and (
            value.get("schema_version") == 6 or value.get("kind") == DISCARD_HAND_MANA_SOURCE
        )

    if not handlers and not any(has_whole_hand(row.get("fixed_mana_result")) for row in catalogs):
        return ()
    if (len(catalogs) != 1 or len(handlers) != 1 or len(program.handlers) != 2
            or program.event != "activate" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.event_condition is not None or program.effects):
        raise ValueError("Whole-hand mana needs its matching exact catalog and mana handler")
    spec, = fixed_mana_specs_from_descriptors(handlers)
    expected = replace(spec.to_activated_ability(), builtin_semantic_key=program.key)
    if (ActivatedAbility.from_dict(thaw_value(catalogs[0])) != expected
            or thaw_value(program.cost_schema) != activated_ability_cost(expected)):
        raise ValueError("Whole-hand mana activation changed its sacrifice, discard or instant timing")
    return spec.result.capability_dependencies


__all__ = [
    "FixedActivatedManaAbilityHandler",
    "FixedManaAbilityRegistry",
    "default_fixed_mana_ability_registry",
    "fixed_mana_specs_from_descriptors",
]
