from __future__ import annotations

"""Copiable physical-composition values through the existing layer owner."""

from dataclasses import replace
from collections.abc import Mapping

from .abilities import ActivatedAbility
from .ability_fragments import StaticComponentSpec,ability_fragment_to_dict, canonical_ability_fragments
from .composition_storage import PermanentCompositionStorage
from .copiable_program_source import CopiableProgramSourceSpec
from .copiable_static_instance import CopiableStaticInstanceSpec
from .entry_copy import COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD
from .replacement.immutable import thaw_value


def composition_copiable_values(storage: PermanentCompositionStorage):
    if not isinstance(storage, PermanentCompositionStorage) or not storage.merges:
        raise ValueError('Composed characteristics require a current physical merge history')
    values=thaw_value(storage.parts[0].copiable_values)
    keywords=[];fragments=[];abilities=[]
    for ordinal,part in enumerate(storage.parts):
        current=part.copiable_values
        keywords.extend(current.get('keywords',()))
        current_fragments=canonical_ability_fragments(current.get('ability_fragments',()))
        fragments.extend(current_fragments)
        oracle_id=current.get(COPIABLE_ORACLE_FIELD,part.card_state['oracle_id'])
        if (not tracks_copied_static_instances(current_fragments)
                and not oracle_id.startswith(('custom-token:','custom-copy:','custom-emblem:'))):
            fragments.append(CopiableProgramSourceSpec(oracle_id,current.get(COPIABLE_FACE_FIELD,'front')))
        for raw in current.get('activated_abilities',()):
            ability=ActivatedAbility.from_dict(thaw_value(raw))
            abilities.append(replace(ability,ability_id=f'composition:{ordinal}:{ability.ability_id}',
                builtin_semantic_key=ability.builtin_semantic_key or f'{oracle_id}:ability:{ability.ability_id}').to_dict())
    values['keywords']=list(dict.fromkeys(keywords))
    values['ability_fragments']=[ability_fragment_to_dict(fragment)
        for fragment in canonical_ability_fragments(fragments)]
    values['activated_abilities']=abilities
    return values


def composition_copy_effect(card):
    storage=card.permanent_composition
    if storage is None:return None
    storage.validate_carrier(card)
    from .characteristic_evaluation import _copy_effect
    effect=_copy_effect(card,composition_copiable_values(storage))
    if effect is None:raise ValueError('A current composition requires its copiable characteristic effect')
    return replace(effect,effect_id=f'{card.object_id}:composition',timestamp=storage.merges[-1].timestamp)


def copiable_source_trigger_programs(host,characteristics,active_zone):
    """Resolve copied source/face data through the existing semantic registry."""
    from .card_program_faces import normalized_program_face_id
    fragments=canonical_ability_fragments(characteristics.get('ability_fragments',()))
    instances=tuple(fragment for fragment in fragments if isinstance(fragment,CopiableStaticInstanceSpec))
    if instances:
        programs=[]
        for instance in instances:
            program=host.semantics.get(instance.semantic_key)
            if program is None:raise ValueError('Copied ability instance references an unknown compiled program')
            if program.active_zone==active_zone and not program.provenance.get('granted_only'):programs.append(program)
        return programs
    references=tuple(fragment for fragment in fragments if isinstance(fragment,CopiableProgramSourceSpec))
    if not references:return None
    programs=[]
    for reference in references:
        record=host.card_db.by_oracle_id(reference.oracle_id)
        if record is None:raise ValueError('Copiable ability source is absent from the pinned database')
        programs.extend(program for program in host.semantics.programs_for_oracle(reference.oracle_id,
            active_zone=active_zone) if not program.provenance.get('granted_only')
            and normalized_program_face_id(record,program)==reference.face_id)
    return programs


def copied_static_fragments(values,creation_timestamp,*,participating=False):
    """Rebase copied static occurrences when their layer-1a effect applies."""
    fragments=canonical_ability_fragments(values)
    if not participating and not tracks_copied_static_instances(fragments):return fragments
    if type(creation_timestamp) is not int or creation_timestamp<0:
        raise ValueError('Copied static abilities require their creating copy effect timestamp')
    retained=tuple(fragment for fragment in fragments if not isinstance(fragment,CopiableStaticInstanceSpec))
    instances=tuple(CopiableStaticInstanceSpec(fragment.semantic_key,creation_timestamp)
        for fragment in retained if isinstance(fragment,StaticComponentSpec) and fragment.schema_version==1)
    return canonical_ability_fragments((*retained,*instances))


def composition_day_night_bound_modes(host,card):
    """Read current inherited bound abilities, independently of physical faces."""
    from .permanent_transform import _bound_program_mode
    from .semantic_runtime.current_ability_components import program_has_current_ability_fragments
    storage=card.permanent_composition
    if storage is None:raise ValueError('Inherited bound abilities require a stored composition')
    storage.validate_carrier(card)
    if card.zone!='battlefield' or card.phased_out or card.face_down:return frozenset()
    values=host._effective_card_data(card)
    programs=copiable_source_trigger_programs(host,values,'battlefield') or ()
    modes=set()
    for program in programs:
        mode=_bound_program_mode(program)
        if mode is None or not program_has_current_ability_fragments(program,values):continue
        if not host.semantic_program_is_current_trusted(program):
            raise ValueError('An inherited bound ability requires its current trusted compiled program')
        modes.add(mode)
    return frozenset(modes)


def copied_static_component_instances(values,applicable_keys):
    """Read surviving copied instances without changing applicability identity."""
    from collections import Counter
    fragments=canonical_ability_fragments(values);allowed=set(applicable_keys)
    components=Counter(fragment.semantic_key for fragment in fragments
        if isinstance(fragment,StaticComponentSpec) and fragment.schema_version==1)
    instances=tuple(fragment for fragment in fragments if isinstance(fragment,CopiableStaticInstanceSpec)
        and fragment.semantic_key in allowed)
    counts=Counter(fragment.semantic_key for fragment in instances)
    if any(count!=components[key] for key,count in counts.items()):
        raise ValueError('Copied static instances must match surviving component multiplicity')
    return tuple((fragment.semantic_key,fragment.creation_timestamp) for fragment in instances)


def _has_copiable_program_sources(values):
    return isinstance(values,(tuple,list)) and any(isinstance(value,CopiableProgramSourceSpec)
        or isinstance(value,Mapping) and value.get('kind')=='copiable_program_source' for value in values)


def tracks_copied_static_instances(values):
    return _has_copiable_program_sources(values) or isinstance(values,(tuple,list)) and any(
        isinstance(value,CopiableStaticInstanceSpec)
        or isinstance(value,Mapping) and value.get('kind')=='copiable_static_instance' for value in values)


def copied_static_source_fragments(host,card):
    """Read a copied source at layer one without entering static collection."""
    from .continuous_effect_model import Layer
    from .continuous_effect_state import active_resolution_effects
    from .errors import GameRuleError
    if card.zone!='battlefield':return None
    copies=tuple(effect for effect in active_resolution_effects(host.state,card) if effect.layer==Layer.COPY)
    annotations=card.annotations
    planes=tuple(value for key in ('copy_overrides','object_characteristics','token_characteristics')
        if isinstance(value:=annotations.get(key),Mapping))
    participating=(card.permanent_composition is not None
        or any(_has_copiable_program_sources(value.get('ability_fragments',())) for value in planes)
        or any(operation.op=='copy_values' and isinstance(operation.value,Mapping)
            and _has_copiable_program_sources(operation.value.get('ability_fragments',()))
            for effect in copies for operation in effect.operations))
    if not participating:return None
    base=host._compiled_base_characteristics(card,host.card_record(card),error_type=GameRuleError)
    values=host._apply_layered_characteristic_annotations(card,base,runtime_effects=copies,
        maximum_layer=Layer.COPY,_enforce_static_component_applicability=False)
    return canonical_ability_fragments(values.get('ability_fragments',()))
