from __future__ import annotations

"""CR 702.16n: a protection instance may retain its granting Aura."""

from dataclasses import replace
from typing import Mapping

CAPABILITY='protection.attachment.source_aura_exception'
SOURCE_AURA='$source'
CHOSEN_AURA_PROTECTION_CAPABILITY='protection.attachment.source_chosen_color'
CHOSEN_AURA_PROTECTION_HANDLER='continuous.attached.source-chosen-color-protection.v1'


def validate_protection_attachment_exception(spec):
    from .ability_fragments import AbilityFragmentError,ProtectionQualityKind
    if spec.schema_version != 3:
        if spec.exempt_aura_incarnation is not None:
            raise AbilityFragmentError('Legacy protection cannot carry an Aura exception')
        return
    if (spec.quality_kind is not ProtectionQualityKind.COLOR or spec.source_predicate is not None
            or type(spec.exempt_aura_incarnation) is not str or not spec.exempt_aura_incarnation):
        raise AbilityFragmentError('Version-three protection requires a fixed color and one source Aura incarnation')


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


def protection_specs_for_retained_aura(specs,logical_object_id):
    if type(logical_object_id) is not str or not logical_object_id:
        raise ValueError('Retained Aura requires a logical incarnation')
    if any(spec.exempt_aura_incarnation==SOURCE_AURA for spec in specs):
        raise ValueError('Protection attachment exception source is unbound')
    return tuple(spec for spec in specs if spec.exempt_aura_incarnation!=logical_object_id)


def attached_protection_runtime_dependencies(program):
    def contains(value):
        if isinstance(value,Mapping):
            if (value.get('kind')=='protection' and isinstance(value.get('value'),Mapping)
                    and value['value'].get('schema_version')==3):return True
            return any(contains(child) for child in value.values())
        if isinstance(value,(list,tuple)):return any(contains(child) for child in value)
        return False
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
        required.add(CAPABILITY)
    return required
