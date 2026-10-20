from __future__ import annotations

"""Closed compiler-pinned descriptors for activated-ability discovery."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..abilities import ActivatedAbility
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import SemanticNodeError


ACTIVATED_ABILITY_CATALOG_HANDLER_ID = "activation.catalog.pinned.v1"


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
            if compiled.reconfigure and not required:
                raise ValueError('Reconfigure requires complete-card admission')
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
    requires_complete_card_program = requires_complete_card_program or ability.announced_x_symbols is not None or ability.reconfigure
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
    "activated_abilities_from_descriptors",
    "activated_ability_catalog_descriptor",
    "default_activated_ability_catalog_registry",
    "is_structural_activated_ability_catalog_program",
]
