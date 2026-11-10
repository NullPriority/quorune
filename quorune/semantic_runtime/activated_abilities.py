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
    if any(field not in ability or ability_local_mana_reduction_from_dict(thaw_value(ability[field])) != reduction
           for ability in catalogs):
        raise ValueError('Ability-local reduction differs between the catalog and activation cost')
    return ability_local_mana_reduction_capabilities(reduction, program.active_zone)


def fixed_mana_spend_rule_runtime_dependencies(program: Any) -> tuple[str, ...]:
    from ..fixed_mana_abilities import FIXED_MANA_SPEND_RULE_HANDLER_ID, FixedActivatedManaAbilitySpec
    from ..mana_spend_rules import MANA_SPEND_RULE_CAPABILITY, is_typed_mana_spend_rule

    catalogs = [descriptor["ability"] for descriptor in program.handlers
                if descriptor.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID
                and is_typed_mana_spend_rule(descriptor["ability"].get("mana_spend_restriction"))]
    if not catalogs:
        return ()
    matching = [replace(FixedActivatedManaAbilitySpec.from_dict(descriptor["ability"]).to_activated_ability(),
                        builtin_semantic_key=program.key)
                for descriptor in program.handlers if descriptor.get("handler_id") == FIXED_MANA_SPEND_RULE_HANDLER_ID]
    if (program.event != "activate" or len(catalogs) != 1 or len(matching) != 1
            or ActivatedAbility.from_dict(catalogs[0]) != matching[0]):
        raise ValueError("Typed mana catalog requires its matching fixed-output handler")
    return (MANA_SPEND_RULE_CAPABILITY,)


def activated_catalog_runtime_dependencies(program):
    return (*ability_local_mana_reduction_runtime_dependencies(program),
            *fixed_mana_spend_rule_runtime_dependencies(program))


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
            if compiled.announced_x_symbols is not None and not required:
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
    requires_complete_card_program = (requires_complete_card_program or ability.announced_x_symbols is not None
        or ability.ability_local_mana_reduction is not None
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
