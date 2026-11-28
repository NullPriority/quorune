from __future__ import annotations

"""CR 702.16n: a protection instance may retain its granting Aura."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Mapping

CAPABILITY='protection.attachment.source_aura_exception'
FIXED_TYPE_AURA_PROTECTION_CAPABILITY='protection.attachment.source_aura_fixed_type'
SOURCE_AURA='$source'
CHOSEN_AURA_PROTECTION_CAPABILITY='protection.attachment.source_chosen_color'
CHOSEN_AURA_PROTECTION_HANDLER='continuous.attached.source-chosen-color-protection.v1'
CONTROLLED_ATTACHMENT_PROTECTION_CAPABILITY = "protection.attachment.source_chosen_color_retention"
CONTROLLED_ATTACHMENT_PROTECTION_HANDLER = "continuous.attached.source-chosen-color-protection.v2"
CONTROLLED_ATTACHMENT_PROTECTION_VERSION = 5
PROTECTION_ATTACHMENT_CUTOFF_FIELD = "controlled_protection_attachment_cutoff"
_BATCH_ENTRIES = ContextVar("controlled_protection_batch_entries", default=None)


@dataclass(frozen=True, slots=True)
class ProtectionAttachmentRetention:
    source_incarnation: str
    target_incarnation: str
    controller: str
    cutoff: int

    def __post_init__(self):
        self._validate_incarnation(self.source_incarnation)
        self._validate_incarnation(self.target_incarnation)
        if self.source_incarnation == self.target_incarnation:
            raise ValueError("Protection attachment source and target must differ")
        if type(self.controller) is not str or not self.controller or self.controller.startswith("$"):
            raise ValueError("Protection attachment retention requires a controller")
        if type(self.cutoff) is not int or self.cutoff < 0:
            raise ValueError("Protection attachment retention requires a committed cutoff")

    @staticmethod
    def _validate_incarnation(value):
        if type(value) is not str:
            raise ValueError("Protection attachment retention requires object incarnations")
        object_id, separator, sequence = value.rpartition("@")
        if not separator or not object_id or not sequence.isascii() or not sequence.isdecimal():
            raise ValueError("Protection attachment retention requires object incarnations")

    def to_dict(self):
        return {
            "source_incarnation": self.source_incarnation, "target_incarnation": self.target_incarnation,
            "controller": self.controller, "cutoff": self.cutoff,
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "source_incarnation", "target_incarnation", "controller", "cutoff",
        }:
            raise ValueError("Protection attachment retention has closed fields")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CurrentProtectionAttachment:
    incarnation: str
    target_incarnation: str
    controller: str
    timestamp: int

    def is_retained_by(self, retention: ProtectionAttachmentRetention):
        return (
            self.target_incarnation == retention.target_incarnation
            and self.controller == retention.controller
            and self.timestamp <= retention.cutoff
        )


@contextmanager
def simultaneous_protection_attachment_entries(state, entries):
    marker = _BATCH_ENTRIES.set((state, frozenset(entries)))
    try:
        yield
    finally:
        _BATCH_ENTRIES.reset(marker)


def simultaneous_attachment_protection_sources(state, entering):
    active = _BATCH_ENTRIES.get()
    if active is None or active[0] is not state or entering.zone == "battlefield" or entering.object_id not in active[1]:
        return frozenset()
    return active[1]


def simultaneous_attached_protection_cutoff(source, target_incarnation):
    data = source.annotations.get(PROTECTION_ATTACHMENT_CUTOFF_FIELD)
    if data is None:
        return source.zone_timestamp
    if not isinstance(data, Mapping) or set(data) != {"source", "target", "timestamp", "cutoff"}:
        raise ValueError("Protection attachment cohort has closed fields")
    if (data["source"], data["target"], data["timestamp"]) != (
        source.logical_object_id, target_incarnation, source.zone_timestamp,
    ):
        return source.zone_timestamp
    if type(data["cutoff"]) is not int or data["cutoff"] < source.zone_timestamp:
        raise ValueError("Protection attachment cohort has an invalid cutoff")
    return data["cutoff"]


def validate_protection_attachment_exception(spec):
    from .ability_fragments import AbilityFragmentError,ProtectionQualityKind
    if spec.schema_version == CONTROLLED_ATTACHMENT_PROTECTION_VERSION:
        if (spec.quality_kind is not ProtectionQualityKind.COLOR or spec.source_predicate is not None
                or spec.exempt_aura_incarnation is not None
                or not isinstance(spec.attachment_retention, ProtectionAttachmentRetention)):
            raise AbilityFragmentError("Controlled attachment protection requires its exact chosen-color source")
        return
    if spec.attachment_retention is not None:
        raise AbilityFragmentError("Older protection cannot carry a controlled attachment retention window")
    if spec.schema_version != 3:
        if spec.exempt_aura_incarnation is not None:
            raise AbilityFragmentError('Legacy protection cannot carry an Aura exception')
        return
    if (spec.quality_kind not in {ProtectionQualityKind.COLOR,ProtectionQualityKind.CARD_TYPE} or spec.source_predicate is not None
            or type(spec.exempt_aura_incarnation) is not str or not spec.exempt_aura_incarnation):
        raise AbilityFragmentError('Version-three protection requires a fixed color or card type and one source Aura incarnation')


def bind_attached_protection_exceptions(node,source_logical_object_id):
    from .ability_fragments import ability_fragment_from_dict,ability_fragment_to_dict
    values=[];changed=False
    for raw in node.add_ability_fragments:
        if raw.get('kind')!='protection' or raw.get('value',{}).get('schema_version')!=3:
            values.append(raw);continue
        spec=ability_fragment_from_dict(raw)
        if spec.exempt_aura_incarnation!=SOURCE_AURA or not source_logical_object_id:
            raise ValueError('Attached protection definitions require the current source Aura identity')
        values.append(ability_fragment_to_dict(replace(spec,exempt_aura_incarnation=source_logical_object_id)))
        changed=True
    return replace(node,add_ability_fragments=tuple(values)) if changed else node


def protection_specs_for_current_attachment(
    specs, *, retaining_aura=None, retaining_attachment=None, simultaneous_sources=(),
):
    if retaining_aura is not None:
        specs = protection_specs_for_retained_aura(specs, retaining_aura)
    return tuple(spec for spec in specs if not (
        spec.attachment_retention is not None and (
            retaining_attachment is not None and retaining_attachment.is_retained_by(spec.attachment_retention)
            or spec.attachment_retention.source_incarnation.rpartition("@")[0] in simultaneous_sources
        )
    ))


def protection_specs_for_retained_aura(specs,logical_object_id):
    if type(logical_object_id) is not str or not logical_object_id:
        raise ValueError('Retained Aura requires a logical incarnation')
    if any(spec.exempt_aura_incarnation==SOURCE_AURA for spec in specs):
        raise ValueError('Protection attachment exception source is unbound')
    return tuple(spec for spec in specs if spec.exempt_aura_incarnation!=logical_object_id)


def attached_protection_runtime_dependencies(program):
    def contains(value, version=3):
        if isinstance(value,Mapping):
            if (value.get('kind')=='protection' and isinstance(value.get('value'),Mapping)
                    and value['value'].get('schema_version')==version):return True
            return any(contains(child, version) for child in value.values())
        if isinstance(value,(list,tuple)):return any(contains(child, version) for child in value)
        return False
    if contains((getattr(program, 'effects', ()), getattr(program, 'handlers', ())),
                CONTROLLED_ATTACHMENT_PROTECTION_VERSION):
        raise ValueError("Controlled attachment retention must be generated by the live chosen-color handler")
    if contains(getattr(program,'effects',())):
        raise ValueError('Source-Aura protection exceptions require a live attached definition')
    required=set()
    for descriptor in getattr(program,'handlers',()):
        if not contains(descriptor):continue
        if not isinstance(descriptor,Mapping) or descriptor.get('handler_id')!='continuous.attached.fixed-characteristics.v1':
            raise ValueError('Source-Aura protection exception has an unsupported definition owner')
        modifier=descriptor.get('modifier')
        if not isinstance(modifier,Mapping):
            raise ValueError('Source-Aura protection requires an attached modifier')
        from .ability_fragments import ability_fragment_from_dict
        for raw in modifier.get('add_ability_fragments',()):
            if not contains(raw):continue
            spec=ability_fragment_from_dict(raw)
            if spec.exempt_aura_incarnation!=SOURCE_AURA:
                raise ValueError('Source-Aura definitions cannot pin an unrelated incarnation')
            if spec.quality_kind.value=='card_type':required.add(FIXED_TYPE_AURA_PROTECTION_CAPABILITY)
        required.add(CAPABILITY)
    return required
