from __future__ import annotations

"""Typed current-or-last-known references to an attached object."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .attachments import attached_object_identity
from .model import CardInstance


class AttachmentReferenceError(ValueError):
    """A compiled attachment reference or its pinned snapshot is invalid."""


ATTACHMENT_INFORMATION_CAPABILITY = 'object.reference.source_attachment_information'
ATTACHMENT_INFORMATION_VERSION = 'source_attachment_information_version'


class AttachmentReferenceKind(str, Enum):
    ENCHANTED = "enchanted_object"
    EQUIPPED = "equipped_object"
    FORTIFIED = "fortified_object"


_PERMANENT_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "land",
        "permanent",
        "planeswalker",
    }
)
_SPEC_FIELDS = {
    "kind",
    "relation",
    "required_card_type",
    "schema_version",
}


def _identity(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise AttachmentReferenceError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class AttachmentReferenceSpec:
    """One closed semantic reference to the object a source is attached to."""

    relation: AttachmentReferenceKind
    required_card_type: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.relation, AttachmentReferenceKind):
            raise AttachmentReferenceError(
                "Attachment reference relation is unsupported"
            )
        if self.required_card_type not in _PERMANENT_TYPES:
            raise AttachmentReferenceError(
                "Attachment reference card type is unsupported"
            )
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise AttachmentReferenceError(
                "Attachment reference schema version is unsupported"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "source_attachment",
            "relation": self.relation.value,
            "required_card_type": self.required_card_type,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttachmentReferenceSpec":
        if not isinstance(value, Mapping) or set(value) != _SPEC_FIELDS:
            raise AttachmentReferenceError(
                "Attachment reference fields are invalid"
            )
        if value.get("kind") != "source_attachment":
            raise AttachmentReferenceError(
                "Attachment reference kind is unsupported"
            )
        try:
            relation = AttachmentReferenceKind(value.get("relation"))
        except (TypeError, ValueError) as exc:
            raise AttachmentReferenceError(
                "Attachment reference relation is unsupported"
            ) from exc
        return cls(
            relation=relation,
            required_card_type=value.get("required_card_type"),
            schema_version=value.get("schema_version"),
        )


@dataclass(frozen=True, slots=True)
class AttachmentObjectIdentity:
    object_id: str
    logical_object_id: str
    reference: str

    def __post_init__(self) -> None:
        _identity(self.object_id, field="Attachment physical identity")
        _identity(self.logical_object_id, field="Attachment logical identity")
        _identity(self.reference, field="Attachment public reference")

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "reference": self.reference,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "AttachmentObjectIdentity":
        if not isinstance(value, Mapping) or set(value) != {
            "object_id",
            "logical_object_id",
            "reference",
        }:
            raise AttachmentReferenceError(
                "Attachment object identity fields are invalid"
            )
        return cls(
            object_id=value.get("object_id"),
            logical_object_id=value.get("logical_object_id"),
            reference=value.get("reference"),
        )


@dataclass(frozen=True, slots=True)
class SourceAttachmentSnapshot:
    """Immutable relation facts pinned when a stack object is created."""

    relation: AttachmentReferenceKind
    source: AttachmentObjectIdentity
    attached_object: AttachmentObjectIdentity | None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.relation, AttachmentReferenceKind):
            raise AttachmentReferenceError(
                "Attachment snapshot relation is unsupported"
            )
        if not isinstance(self.source, AttachmentObjectIdentity):
            raise AttachmentReferenceError(
                "Attachment snapshot source identity is invalid"
            )
        if self.attached_object is not None and not isinstance(
            self.attached_object, AttachmentObjectIdentity
        ):
            raise AttachmentReferenceError(
                "Attachment snapshot target identity is invalid"
            )
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AttachmentReferenceError(
                "Attachment snapshot schema version is unsupported"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "relation": self.relation.value,
            "source": self.source.to_dict(),
            "attached_object": (
                self.attached_object.to_dict()
                if self.attached_object is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceAttachmentSnapshot":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "relation",
            "source",
            "attached_object",
        }:
            raise AttachmentReferenceError(
                "Attachment snapshot fields are invalid"
            )
        try:
            relation = AttachmentReferenceKind(value.get("relation"))
        except (TypeError, ValueError) as exc:
            raise AttachmentReferenceError(
                "Attachment snapshot relation is unsupported"
            ) from exc
        attached = value.get("attached_object")
        return cls(
            relation=relation,
            source=AttachmentObjectIdentity.from_dict(value.get("source")),
            attached_object=(
                AttachmentObjectIdentity.from_dict(attached)
                if attached is not None
                else None
            ),
            schema_version=value.get("schema_version"),
        )


def attachment_reference_specs(value: Any) -> tuple[AttachmentReferenceSpec, ...]:
    """Return every closed attachment reference nested in semantic values."""

    if isinstance(value, Mapping):
        if value.get("kind") == "source_attachment":
            return (AttachmentReferenceSpec.from_dict(value),)
        return tuple(
            spec
            for child in value.values()
            for spec in attachment_reference_specs(child)
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            spec
            for child in value
            for spec in attachment_reference_specs(child)
        )
    return ()


def required_attachment_relation(
    effects: Sequence[Mapping[str, Any]],
) -> AttachmentReferenceKind | None:
    """Return the one relation required by a semantic program, if any."""

    specs = attachment_reference_specs(effects)
    if not specs:
        return None
    relations = {spec.relation for spec in specs}
    if len(relations) != 1:
        raise AttachmentReferenceError(
            "One semantic program cannot mix attachment reference relations"
        )
    return next(iter(relations))


def attachment_information_capabilities(value):
    return (ATTACHMENT_INFORMATION_CAPABILITY,) if any(
        spec.schema_version == 2 for spec in attachment_reference_specs(value)
    ) else ()


def attachment_information_payload_dependencies(value):
    if isinstance(value, Mapping) and value.get('kind')=='source_attachment':
        spec=AttachmentReferenceSpec.from_dict(value)
        return (ATTACHMENT_INFORMATION_CAPABILITY,) if spec.schema_version==2 else ()
    return ()


def _object_identity(card: CardInstance) -> AttachmentObjectIdentity:
    return AttachmentObjectIdentity(
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
        reference=card.ref,
    )


def capture_source_attachment_snapshot(
    cards: Mapping[str, CardInstance],
    source: CardInstance,
    relation: AttachmentReferenceKind,
) -> SourceAttachmentSnapshot:
    """Capture the authoritative live relation before costs or enqueueing."""

    if cards.get(source.object_id) is not source:
        raise AttachmentReferenceError(
            "Attachment snapshot source is not authoritative"
        )
    identity = attached_object_identity(cards, source)
    target = cards.get(identity.object_id) if identity is not None else None
    return SourceAttachmentSnapshot(
        relation=relation,
        source=_object_identity(source),
        attached_object=_object_identity(target) if target is not None else None,
    )


def capture_last_known_attachment_snapshot(
    cards: Mapping[str, CardInstance],
    source: CardInstance,
    relation: AttachmentReferenceKind,
    *,
    source_logical_object_id: str,
    attached_to_ref: str | None,
) -> SourceAttachmentSnapshot:
    """Capture a normalized zone event's previous attachment relation."""

    _identity(
        source_logical_object_id,
        field="Last-known attachment source logical identity",
    )
    target = next(
        (
            card
            for card in cards.values()
            if attached_to_ref is not None and card.ref == attached_to_ref
        ),
        None,
    )
    if attached_to_ref is not None and target is None:
        raise AttachmentReferenceError(
            "Last-known attachment target reference is stale"
        )
    return SourceAttachmentSnapshot(
        relation=relation,
        source=AttachmentObjectIdentity(
            object_id=source.object_id,
            logical_object_id=source_logical_object_id,
            reference=source.ref,
        ),
        attached_object=_object_identity(target) if target is not None else None,
    )


def resolve_source_attachment(
    cards: Mapping[str, CardInstance],
    snapshot_value: Mapping[str, Any],
    spec: AttachmentReferenceSpec,
    *,
    source_object_id: str,
    source_logical_object_id: str,
) -> CardInstance | None:
    """Resolve the live relation or its pinned LKI target without mutation."""

    snapshot = SourceAttachmentSnapshot.from_dict(snapshot_value)
    if snapshot.relation is not spec.relation:
        raise AttachmentReferenceError(
            "Attachment reference and snapshot relations disagree"
        )
    if (
        snapshot.source.object_id != source_object_id
        or snapshot.source.logical_object_id != source_logical_object_id
    ):
        raise AttachmentReferenceError(
            "Attachment snapshot source identity is stale or tampered"
        )
    source = cards.get(source_object_id)
    if (
        source is not None
        and source.logical_object_id == source_logical_object_id
    ):
        if source.zone != "battlefield" or source.phased_out:
            return None
        identity = attached_object_identity(cards, source)
        return cards.get(identity.object_id) if identity is not None else None
    target_identity = snapshot.attached_object
    if target_identity is None:
        return None
    target = cards.get(target_identity.object_id)
    if (
        target is None
        or target.logical_object_id != target_identity.logical_object_id
        or target.ref != target_identity.reference
        or target.zone != "battlefield"
        or target.phased_out
    ):
        return None
    return target


__all__ = [
    "AttachmentObjectIdentity",
    "AttachmentReferenceError",
    "AttachmentReferenceKind",
    "AttachmentReferenceSpec",
    "SourceAttachmentSnapshot",
    "attachment_reference_specs",
    "capture_last_known_attachment_snapshot",
    "capture_source_attachment_snapshot",
    "required_attachment_relation",
    "resolve_source_attachment",
]
