from __future__ import annotations

"""Attack-only haste permission supplied by a current Aura ability."""

from dataclasses import dataclass
from enum import StrEnum

from ..ability_fragments import static_component_keys
from ..attachments import attached_object_identity
from ..permanent_designations import permanent_numeric_designations
from ..semantic_runtime.component_registry import exact_fields
from ..semantic_runtime.context import SemanticNodeError


ATTACHED_ATTACK_HASTE_CAPABILITY = "combat.attack.permission.enchanted_creature_as_haste"
ATTACHED_ATTACK_HASTE_HANDLER = "permission.attack.enchanted-creature-as-haste.v1"
ATTACK_PERMISSION_EVENT = "combat.attack.permission"


class AttackHasteScope(StrEnum):
    ENCHANTED_CREATURE = "enchanted_creature"


@dataclass(frozen=True, slots=True)
class AttachedAttackHastePermissionHandler:
    handler_id: str = ATTACHED_ATTACK_HASTE_HANDLER
    schema_version: int = 1
    family: str = ATTACHED_ATTACK_HASTE_CAPABILITY
    event: str = ATTACK_PERMISSION_EVENT
    rule_references: tuple[str, ...] = (
        "302.6", "303.4e", "508.1a", "609.4", "611.3a", "611.3b", "702.10b",
    )
    capability_dependencies: tuple[str, ...] = (ATTACHED_ATTACK_HASTE_CAPABILITY,)

    def validate(self, descriptor) -> AttackHasteScope:
        exact_fields(
            descriptor, {"handler_id", "schema_version", "event", "scope"},
            field="attached attack haste permission",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or descriptor["event"] != self.event
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["scope"] != AttackHasteScope.ENCHANTED_CREATURE.value
        ):
            raise SemanticNodeError("Invalid attached attack haste permission")
        return AttackHasteScope.ENCHANTED_CREATURE

    def lower(self, descriptor, context) -> tuple[AttackHasteScope, ...]:
        return (self.validate(descriptor),)


def current_enchanted_attack_permission(host, card) -> bool:
    """Check the exact attachment and effective Aura ability at declaration."""
    from ..semantic_runtime.continuous_registry import default_continuous_effect_component_registry

    for source_id in card.attachments:
        source = host.state.cards.get(source_id)
        if source is None:
            continue
        attached = attached_object_identity(host.state.cards, source)
        if (
            attached is None
            or attached.object_id != card.object_id
            or attached.logical_object_id != card.logical_object_id
        ):
            continue
        data = host._effective_card_data(source)
        if "aura" not in host._type_parts(data["type_line"])[1]:
            continue
        keys = static_component_keys(
            data.get("ability_fragments", ()),
            source_designations=permanent_numeric_designations(source),
        )
        for key in keys:
            program = host.semantics.get(key)
            if (
                program is None
                or program.event != ATTACK_PERMISSION_EVENT
                or program.active_zone != "battlefield"
                or not host.semantic_program_is_current_trusted(program)
            ):
                continue
            for descriptor in program.handlers:
                if descriptor["handler_id"] == ATTACHED_ATTACK_HASTE_HANDLER:
                    registry = default_continuous_effect_component_registry()
                    if AttackHasteScope.ENCHANTED_CREATURE in registry.lower(descriptor, None):
                        return True
    return False
