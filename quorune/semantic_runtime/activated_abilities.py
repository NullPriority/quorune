from __future__ import annotations

"""Closed compiler-pinned descriptors for activated-ability discovery."""

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Mapping

from ..abilities import ActivatedAbility
from ..activated_ability_descriptor import ABILITY_LOCAL_MANA_REDUCTION_FIELD
from ..activation_cost_modifiers import (
    ability_local_mana_reduction_capabilities, ability_local_mana_reduction_from_dict,
    ability_local_reduction_origin_is_supported,
)
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..replacement.immutable import thaw_value
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


ACTIVATED_ABILITY_CATALOG_HANDLER_ID = "activation.catalog.pinned.v1"


def ability_local_mana_reduction_runtime_dependencies(program: Any) -> tuple[str, ...]:
    cost = getattr(program, 'cost_schema', None) or {}
    catalogs = [descriptor['ability'] for descriptor in program.handlers
                if descriptor.get('handler_id') == ACTIVATED_ABILITY_CATALOG_HANDLER_ID]
    field = ABILITY_LOCAL_MANA_REDUCTION_FIELD
    if field not in cost and not any(field in ability for ability in catalogs):
        return ()
    if program.event != 'activate' or field not in cost:
        raise ValueError('Ability-local reduction requires its activation cost schema')
    reduction = ability_local_mana_reduction_from_dict(thaw_value(cost[field]))
    if not ability_local_reduction_origin_is_supported((program.active_zone,), cost.get('discard_source'), reduction):
        raise ValueError('Ability-local reduction requires a supported activation origin')
    from ..activation_target_values import TargetActivationReduction
    from ..rules.activation.targeted_costs import single_creature_target_price_plan
    if isinstance(reduction, TargetActivationReduction):single_creature_target_price_plan(program.target_schema)
    if any(field not in ability or ability_local_mana_reduction_from_dict(thaw_value(ability[field])) != reduction
           for ability in catalogs):
        raise ValueError('Ability-local reduction differs between the catalog and activation cost')
    return ability_local_mana_reduction_capabilities(reduction, program.active_zone)


def fixed_mana_spend_rule_runtime_dependencies(program: Any) -> tuple[str, ...]:
    from ..fixed_mana_abilities import FIXED_MANA_SPEND_RULE_HANDLER_ID, FixedActivatedManaAbilitySpec
    from ..mana_spend_rules import decode_mana_spend_rule, is_typed_mana_spend_rule
    from ..chosen_color_mana_abilities import ChosenColorActivatedManaAbilitySpec
    from ..chosen_color_mana_model import CHOSEN_COLOR_MANA_HANDLER_ID
    from ..rules.exert_mana import EXERT_MANA_HANDLER_ID

    catalogs = [descriptor["ability"] for descriptor in program.handlers
                if descriptor.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID
                and is_typed_mana_spend_rule(descriptor["ability"].get("mana_spend_restriction"))]
    specs = [FixedActivatedManaAbilitySpec.from_dict(descriptor["ability"])
             for descriptor in program.handlers
             if descriptor.get("handler_id") in (FIXED_MANA_SPEND_RULE_HANDLER_ID, EXERT_MANA_HANDLER_ID)]
    expected = [(spec.to_activated_ability(), spec.spend_restriction) for spec in specs]
    for descriptor in program.handlers:
        if descriptor.get("handler_id") == CHOSEN_COLOR_MANA_HANDLER_ID:
            spec = ChosenColorActivatedManaAbilitySpec.from_dict(descriptor["ability"])
            if is_typed_mana_spend_rule(spec.output.restriction):
                expected.append((spec.to_activated_ability(), spec.output.restriction))
    if catalogs and (program.event != "activate" or len(catalogs) != 1 or len(expected) != 1
            or ActivatedAbility.from_dict(catalogs[0]) != replace(expected[0][0], builtin_semantic_key=program.key)):
        raise ValueError("Typed mana catalog requires its matching fixed-output handler")
    from ..rules.chosen_color_draw_price import chosen_color_draw_runtime_dependencies

    return tuple(sorted({
        *chosen_color_draw_runtime_dependencies(program),
        *(capability for _ability, restriction in expected
          for capability in decode_mana_spend_rule(restriction).capability_dependencies),
    }))


def mill_library_cost_runtime_dependencies(program):
    field = "mill_library_cost"
    price = getattr(program, "cost_schema", None) or {}
    catalogs = [handler["ability"] for handler in program.handlers
                if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID]
    if field not in price and not any(field in ability for ability in catalogs):
        return ()
    if (program.event != "activate" or program.active_zone != "battlefield" or len(catalogs) != 1
            or type(price.get(field)) is not int or price[field] != 1
            or ActivatedAbility.from_dict(thaw_value(catalogs[0])).mill_library_cost != 1
            or not program.effects):
        raise ValueError("A Mill activation requires its catalog and resolving effect to preserve the exact price")
    from ..rules.activation.mill_library_cost import MILL_LIBRARY_ACTIVATION_COST_CAPABILITY

    return (MILL_LIBRARY_ACTIVATION_COST_CAPABILITY,)


def source_counter_placement_runtime_dependencies(program):
    from ..activated_ability_descriptor import (
        SOURCE_COUNTER_PLACEMENT_CAPABILITY, SOURCE_COUNTER_PLACEMENT_FIELD,
        decode_source_counter_placement_cost,
    )

    field = SOURCE_COUNTER_PLACEMENT_FIELD
    cost = getattr(program, "cost_schema", None) or {}
    catalogs = [handler["ability"] for handler in program.handlers
                if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID]
    if field not in cost and not any(field in ability for ability in catalogs):
        return ()
    required = {"text", "mana", "complex_symbols", "tap_source", "untap_source",
                "discard_source", "sacrifice_source", "exile_source", "life_payment",
                "energy_payment", "loyalty_delta", "source_counter_removal_cost",
                "choices", "uncompiled_costs", field}
    if (
        program.event != "activate" or program.active_zone != "battlefield"
        or program.target_schema is not None or program.event_condition is not None
        or len(catalogs) != 1 or len(program.handlers) != 1
        or tuple(program.effects) != ({"op": "untap", "card": "$source.zone_object"},)
        or set(cost) != required
        or cost["loyalty_delta"] is not None
        or not isinstance(cost["mana"], Mapping)
        or cost["mana"] != catalogs[0]["mana"] or any(cost["mana"].values())
        or cost["text"] != catalogs[0]["cost_text"]
        or any(cost[name] for name in required - {"text", "mana", "loyalty_delta", field})
        or decode_source_counter_placement_cost(cost[field])
            != ActivatedAbility.from_dict(thaw_value(catalogs[0])).source_counter_placement_cost
    ):
        raise ValueError("The source counter activation must retain its exact price and untap result")
    return (SOURCE_COUNTER_PLACEMENT_CAPABILITY,)


def land_potential_runtime_dependencies(program):
    from ..compiler.activated_costs import activated_ability_cost
    from ..land_mana_potential import (
        LAND_MANA_POTENTIAL_HANDLER_ID,
        LAND_MANA_POTENTIAL_KIND, LandPotentialActivatedManaSpec,
    )
    catalogs = tuple(handler["ability"] for handler in program.handlers
                     if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID)
    producers = tuple(handler["ability"] for handler in program.handlers
                      if handler.get("handler_id") == LAND_MANA_POTENTIAL_HANDLER_ID)
    has_output = any(isinstance(output := ability.get("dynamic_mana_output"), Mapping)
                     and output.get("kind") == LAND_MANA_POTENTIAL_KIND for ability in catalogs)
    if not producers and not has_output:
        return ()
    if (len(producers) != 1 or len(catalogs) != 1 or len(program.handlers) != 2
            or program.event != "activate" or program.active_zone != "battlefield"
            or program.event_condition is not None or program.target_schema is not None or program.effects):
        raise ValueError("Land mana potential requires its paired output and activation catalog")
    spec = LandPotentialActivatedManaSpec.from_dict(producers[0])
    expected = replace(spec.to_activated_ability(), builtin_semantic_key=program.key)
    if (ActivatedAbility.from_dict(thaw_value(catalogs[0])) != expected
            or thaw_value(program.cost_schema) != activated_ability_cost(expected)):
        raise ValueError("Land mana potential changed its declared output or tap cost")
    return spec.output.capabilities


def land_play_mana_runtime_dependencies(program):
    from ..land_play_mana_model import (
        LAND_PLAY_MANA_CAPABILITY, LAND_PLAY_MANA_HANDLER_ID, LAND_PLAY_MANA_KIND, LandPlayActivatedManaSpec,
    )

    catalogs = [handler["ability"] for handler in program.handlers
                if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID]
    specs = [LandPlayActivatedManaSpec.from_dict(handler["ability"])
             for handler in program.handlers if handler.get("handler_id") == LAND_PLAY_MANA_HANDLER_ID]
    typed_catalog = any(isinstance(catalog.get("dynamic_mana_output"), Mapping)
                        and catalog["dynamic_mana_output"].get("kind") == LAND_PLAY_MANA_KIND
                        for catalog in catalogs)
    if not specs and not typed_catalog:
        return ()
    if (len(specs) != 1 or program.event != "activate" or program.active_zone != "battlefield"
            or program.target_schema is not None or program.event_condition is not None or program.effects):
        raise ValueError("Land-play mana must preserve one closed activated output handler")
    ability = specs[0].to_activated_ability()
    if catalogs and (len(catalogs) != 1 or ActivatedAbility.from_dict(thaw_value(catalogs[0]))
                     != replace(ability, builtin_semantic_key=program.key)):
        raise ValueError("Land-play mana catalog differs from the authoritative output")
    expected = {
        "text": ability.cost_text, "mana": dict(ability.mana), "tap_source": True,
        "untap_source": False, "sacrifice_source": False, "discard_source": False, "exile_source": False,
        "life_payment": 0, "energy_payment": 0, "loyalty_delta": None, "source_counter_removal_cost": None,
        "complex_symbols": [], "choices": [], "uncompiled_costs": [],
    }
    if thaw_value(program.cost_schema) != expected:
        raise ValueError("Land-play mana must preserve the exact compiled tap cost")
    return (LAND_PLAY_MANA_CAPABILITY,)


def activated_catalog_runtime_dependencies(program):
    from ..rules.activation.commander_identity_life import commander_identity_life_draw_runtime_dependencies
    from ..rules.forage import forage_activation_runtime_dependencies
    from ..rules.noted_mana import noted_mana_program_dependencies
    from ..rules.owned_commander_battlefield_return import paid_commander_target_runtime_dependencies
    from ..rules.controller_top_exile_activation import controller_top_exile_runtime_dependencies

    return (*forage_activation_runtime_dependencies(program), *ability_local_mana_reduction_runtime_dependencies(program),
            *fixed_mana_spend_rule_runtime_dependencies(program), *mill_library_cost_runtime_dependencies(program),
            *source_counter_placement_runtime_dependencies(program),
            *land_play_mana_runtime_dependencies(program),
            *land_potential_runtime_dependencies(program), *noted_mana_program_dependencies(program),
            *paid_commander_target_runtime_dependencies(program), *controller_top_exile_runtime_dependencies(program),
            *commander_identity_life_draw_runtime_dependencies(program))


@dataclass(frozen=True, slots=True)
class ActivatedAbilityCatalogHandler:
    """Validate a complete activated ability compiled before game runtime."""

    handler_id: str = ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.catalog"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "602.1",
        "602.2",
        "602.2a",
        "602.2b",
        "602.2c",
        "605.1a",
    )
    capability_dependencies: tuple[str, ...] = ()

    def validate(self, descriptor: Mapping[str, Any]) -> ActivatedAbility:
        required = {REQUIRES_COMPLETE_CARD_PROGRAM_FIELD} if REQUIRES_COMPLETE_CARD_PROGRAM_FIELD in descriptor else set()
        if required and descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True:
            raise SemanticNodeError('Complete-card activation admission must be true')
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "ability"} | required,
            field="activated ability catalog handler",
        )
        if descriptor["handler_id"] != self.handler_id:
            raise SemanticNodeError(
                "Activated ability catalog handler ID mismatch"
            )
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError(
                "Unsupported activated ability catalog schema version"
            )
        if descriptor["event"] != self.event:
            raise SemanticNodeError(
                "Activated ability catalog handlers must use activate"
            )
        ability = descriptor["ability"]
        if not isinstance(ability, Mapping):
            raise SemanticNodeError(
                "Activated ability catalog value must be an object"
            )
        try:
            compiled = ActivatedAbility.from_dict(ability)
            from ..rules.forage import has_forage_cost, validate_forage_activation
            if has_forage_cost(compiled):
                if not required:
                    raise ValueError("Forage requires complete-card admission")
                validate_forage_activation(compiled)
            from ..paid_activation_card_types import has_bounded_sacrifice
            from ..rules.activation.bounded_type_sacrifice import validate_paid_type_ability
            if has_bounded_sacrifice(compiled):
                if not required:
                    raise ValueError("Bounded sacrifice requires complete-card admission")
                validate_paid_type_ability(compiled)
            if compiled.chooses_activation_x and not required:
                raise ValueError('Announced activation X requires complete-card admission')
            if compiled.ability_local_mana_reduction is not None and not required:
                raise ValueError('Ability-local mana reduction requires complete-card admission')
            if compiled.reconfigure and not required:
                raise ValueError('Reconfigure requires complete-card admission')
            if compiled.saddle_threshold is not None and not required:
                raise ValueError("Saddle requires complete-card admission")
            return compiled
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: object,
    ) -> tuple[ActivatedAbility, ...]:
        del context
        return (self.validate(descriptor),)


class ActivatedAbilityCatalogRegistry(
    RuntimeComponentRegistry[object, ActivatedAbility]
):
    pass


@lru_cache(maxsize=1)
def default_activated_ability_catalog_registry(
) -> ActivatedAbilityCatalogRegistry:
    return ActivatedAbilityCatalogRegistry(
        (ActivatedAbilityCatalogHandler(),)
    ).freeze()


def activated_ability_catalog_descriptor(
    ability: ActivatedAbility,
    *, requires_complete_card_program: bool = False,
) -> dict[str, Any]:
    from ..rules.forage import has_forage_cost
    from ..paid_activation_card_types import has_bounded_sacrifice

    requires_complete_card_program = (requires_complete_card_program or has_forage_cost(ability) or ability.chooses_activation_x
        or has_bounded_sacrifice(ability) or ability.ability_local_mana_reduction is not None
        or ability.reconfigure or ability.saddle_threshold is not None)
    return {
        "handler_id": ACTIVATED_ABILITY_CATALOG_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        "ability": ability.to_dict(),
        **({REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True} if requires_complete_card_program else {}),
    }


def activated_abilities_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...]
    | list[Mapping[str, Any]],
) -> tuple[ActivatedAbility, ...]:
    registry = default_activated_ability_catalog_registry()
    abilities: list[ActivatedAbility] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        abilities.extend(registry.lower(descriptor, None))
    return tuple(abilities)


def is_structural_activated_ability_catalog_program(program: Any) -> bool:
    """Whether a program carries discovery metadata but no resolution rules."""

    handlers = tuple(getattr(program, "handlers", ()))
    return bool(
        str(getattr(program, "ability_id", "")).startswith(
            "ability:catalog:"
        )
        and not getattr(program, "effects", ())
        and len(handlers) == 1
        and handlers[0].get("handler_id")
        == ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    )


__all__ = [
    "ACTIVATED_ABILITY_CATALOG_HANDLER_ID",
    "ActivatedAbilityCatalogHandler",
    "ActivatedAbilityCatalogRegistry",
    "ability_local_mana_reduction_runtime_dependencies",
    "activated_abilities_from_descriptors",
    "activated_ability_catalog_descriptor",
    "default_activated_ability_catalog_registry",
    "is_structural_activated_ability_catalog_program",
]
