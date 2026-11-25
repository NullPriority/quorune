from __future__ import annotations

"""Closed Aura targets qualified by their current battlefield attachment."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import thaw_value


AURA_ATTACHMENT_TARGET_FIELD = "aura_attachment"
AURA_ATTACHMENT_TARGET_CAPABILITY = "target.object.aura_attached_to_permanent"


@dataclass(frozen=True, slots=True)
class AuraAttachmentTargetSpec:
    attached_type: str
    attached_controller: str = "any"
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or type(self.attached_type) is not str or self.attached_type not in {"creature", "land"}
            or type(self.attached_controller) is not str or self.attached_controller not in {"any", "you"}
            or self.attached_type == "land" and self.attached_controller != "any"
        ):
            raise ValueError("Aura attachment targeting requires its closed land or creature relation")

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "attached_type", "attached_controller"}:
            raise ValueError("Aura attachment targeting requires its complete descriptor")
        return cls(**dict(value))

    @property
    def slug(self):
        suffix = "-you-control" if self.attached_controller == "you" else ""
        return f"aura-attached-to-{self.attached_type}{suffix}"

    def to_dict(self):
        return {"schema_version": self.schema_version, "attached_type": self.attached_type,
                "attached_controller": self.attached_controller}

    def to_target_schema(self):
        return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["enchantment"],
                "subtypes_any": ["aura"], "count": 1, AURA_ATTACHMENT_TARGET_FIELD: self.to_dict()}


def validate_aura_attachment_target_group(group):
    if (
        not isinstance(group.aura_attachment, AuraAttachmentTargetSpec)
        or group.zones != ("battlefield",) or group.categories != ("permanent",)
        or group.types_any != ("enchantment",) or group.subtypes_any != ("aura",)
        or group.min_targets != 1 or group.max_targets != 1
    ):
        raise ValueError("An attachment-qualified target requires one battlefield Aura")


def aura_attachment_target_matches(host, spec, aura, *, actor):
    from ..attachments import attached_object_identity

    if aura is None:
        return False
    identity = attached_object_identity(host.state.cards, aura)
    if identity is None:
        return False
    attached = host.state.cards[identity.object_id]
    if spec.attached_controller == "you" and attached.controller != actor:
        return False
    types, _, _ = host._type_parts(str(host._effective_card_data(attached).get("type_line") or ""))
    return spec.attached_type in types


AURA_ATTACHMENT_DESTRUCTION_DEPENDENCIES = (
    AURA_ATTACHMENT_TARGET_CAPABILITY, "permanent.destroy.effect", "target.revalidate_resolution",
)


def _has_aura_attachment_target(value):
    if isinstance(value, Mapping):
        return AURA_ATTACHMENT_TARGET_FIELD in value or any(_has_aura_attachment_target(child) for child in value.values())
    return isinstance(value, (tuple, list)) and any(_has_aura_attachment_target(child) for child in value)


def aura_attachment_destruction_dependencies(effects, schema):
    if not _has_aura_attachment_target(schema):
        return ()
    if not isinstance(schema, Mapping) or AURA_ATTACHMENT_TARGET_FIELD not in schema:
        raise ValueError("Aura attachment targeting requires one direct target")
    spec = AuraAttachmentTargetSpec.from_mapping(schema[AURA_ATTACHMENT_TARGET_FIELD])
    if thaw_value(schema) != spec.to_target_schema() or thaw_value(effects) != [{"op": "destroy", "card": "$target.0"}]:
        raise ValueError("Aura attachment targeting requires its exact destruction and target")
    return AURA_ATTACHMENT_DESTRUCTION_DEPENDENCIES
