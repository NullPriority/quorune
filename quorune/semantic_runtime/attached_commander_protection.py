"""Protection from the complement of the Equipment controller's commander colors."""

from dataclasses import dataclass

from ..ability_fragments import ProtectionQualityKind, ProtectionSpec, ability_fragment_to_dict
from .attached_continuous import AttachedFixedCharacteristicsHandler
from .component_registry import exact_fields
from .context import SemanticNodeError


COMMANDER_COMPLEMENT_PROTECTION_HANDLER = "continuous.attached.commander_complement_protection.v1"
COMMANDER_COMPLEMENT_PROTECTION_CAPABILITY = "protection.attachment.commander_color_complement"
_COLOR_ORDER = "WUBRG"


def commander_complement_protection_descriptor():
    return {
        "handler_id": COMMANDER_COMPLEMENT_PROTECTION_HANDLER,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "subject": "equipped_creature",
    }


@dataclass(frozen=True, slots=True)
class AttachedCommanderProtectionHandler:
    handler_id: str = COMMANDER_COMPLEMENT_PROTECTION_HANDLER
    schema_version: int = 1
    family: str = "continuous.attached.commander_complement_protection"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("105.1", "109.5", "301.5", "611.3b", "613.1f", "613.4c", "702.16", "903.4", "903.4d")
    capability_dependencies: tuple[str, ...] = (
        COMMANDER_COMPLEMENT_PROTECTION_CAPABILITY, "continuous.attached.fixed_characteristics", "protection.typed.debt",
    )

    def validate(self, descriptor):
        expected = commander_complement_protection_descriptor()
        exact_fields(descriptor, set(expected), field="commander-color Equipment protection")
        if type(descriptor["schema_version"]) is not int or dict(descriptor) != expected:
            raise SemanticNodeError("Commander-color Equipment protection requires its exact equipped-creature descriptor")

    def lower(self, descriptor, context):
        self.validate(descriptor)
        if context.attached_object is None:
            return ()
        identity = context.source_commander_colors
        if identity is None or any(color not in _COLOR_ORDER or len(color) != 1 for color in identity):
            raise SemanticNodeError("Equipment protection requires the source controller's pinned commander colors")
        colors = [color for color in _COLOR_ORDER if color not in identity]
        fragments = [ability_fragment_to_dict(ProtectionSpec(ProtectionQualityKind.COLOR, color)) for color in colors]
        attached = AttachedFixedCharacteristicsHandler()
        return attached.lower({
            "handler_id": attached.handler_id, "schema_version": attached.schema_version, "event": attached.event,
            "condition": {"relation": "source_attached_object", "types_all": ["creature"]},
            "modifier": {
                "type_operations": [], "add_abilities": ["Protection"] if fragments else [],
                "remove_abilities": [], "add_rules_text": [], "add_ability_fragments": fragments,
                "power": 3, "toughness": 3,
            },
        }, context)
