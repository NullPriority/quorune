from __future__ import annotations

"""Layer-four additions with separate controlled-spell and owned-card scopes."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousOperation, Layer
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


CROSS_ZONE_TYPE_ADDITION_HANDLER = "continuous.characteristics.cross-zone-type-addition.v1"
CROSS_ZONE_TYPE_ADDITION_CAPABILITY = "continuous.characteristics.cross_zone_type_addition"
_OWNED_CARD_ZONES = ("command", "exile", "graveyard", "hand", "library", "stack")
_PERMANENT_SPELL_TYPES = tuple(sorted(PERMANENT_CARD_TYPES - {"land"}))


@dataclass(frozen=True, slots=True)
class CrossZoneTypeAdditionSpec:
    added_type: str
    receiver: str
    includes_owned_cards: bool

    def __post_init__(self) -> None:
        if (type(self.added_type) is not str or self.added_type not in {"artifact", "enchantment"}
                or type(self.receiver) is not str or self.receiver not in {"creatures", "nonland_permanents"}
                or type(self.includes_owned_cards) is not bool):
            raise ValueError("Cross-zone type additions require closed type and receiver scopes")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CrossZoneTypeAdditionSpec:
        exact_fields(value, {"added_type", "receiver", "includes_owned_cards"}, field="cross-zone type addition")
        return cls(value["added_type"], value["receiver"], value["includes_owned_cards"])

    def to_dict(self) -> dict[str, Any]:
        return {"added_type": self.added_type, "receiver": self.receiver,
                "includes_owned_cards": self.includes_owned_cards}

    def recipients(self, controller: str) -> tuple[ObjectQuerySpec, ...]:
        if self.receiver == "creatures":
            permanent = spell = owned = {"types_all": ("creature",)}
        else:
            permanent = {"excluded_types": ("land",)}
            spell = {"types_any": _PERMANENT_SPELL_TYPES}
            owned = {**spell, "excluded_types": ("land",)}
        controlled = (
            ObjectQuerySpec(zones=("battlefield",), controller=controller, **permanent),
            ObjectQuerySpec(zones=("stack",), controller=controller, **spell),
        )
        if not self.includes_owned_cards:
            return controlled
        return (*controlled, ObjectQuerySpec(zones=_OWNED_CARD_ZONES, owner=controller, token=False, **owned))


@dataclass(frozen=True, slots=True)
class CrossZoneTypeAdditionHandler:
    handler_id: str = CROSS_ZONE_TYPE_ADDITION_HANDLER
    schema_version: int = 1
    family: str = CROSS_ZONE_TYPE_ADDITION_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.TYPE
    rule_references: tuple[str, ...] = ("109.2a", "109.2b", "110.4a", "110.4b", "205.1b", "611.3b", "613.1d")
    capability_dependencies: tuple[str, ...] = (CROSS_ZONE_TYPE_ADDITION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> CrossZoneTypeAdditionSpec:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program", "addition"},
                     field="cross-zone type addition component")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Cross-zone type additions require their exact handler and complete source")
        return CrossZoneTypeAdditionSpec.from_dict(descriptor["addition"])

    def lower(self, descriptor, context):
        spec = self.validate(descriptor)
        operation = ContinuousOperation("add_types", (spec.added_type.title(),), field="card_types")
        return tuple(
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}:4:{index}",
                source_id=context.source_object_id, layer=Layer.TYPE, sublayer="4",
                timestamp=context.source_timestamp, operations=(operation,),
                origin=ContinuousEffectOrigin.STATIC_ABILITY, applies=predicate,
            )
            for index, predicate in enumerate(spec.recipients(context.source_controller))
        )
