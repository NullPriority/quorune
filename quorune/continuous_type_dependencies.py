from __future__ import annotations

"""Per-object CR613.8 applicability dependencies for fixed type setters."""

from copy import deepcopy
from dataclasses import replace
from .continuous_effect_model import Layer
from .basic_land_types import BASIC_LAND_MANA


def bind_type_applicability_dependencies(effects,base,context):
    from .continuous_effects import _matches,_apply_operation
    values=tuple(effects);result=[]
    def bound_to_object(effect):
        related=effect.related_object
        if related is not None and (related.object_id!=context.get('object_id') or related.logical_object_id!=context.get('logical_object_id')):return False
        if effect.locked_objects and not any(i.object_id==context.get('object_id') and i.logical_object_id==context.get('logical_object_id') for i in effect.locked_objects):return False
        return True
    for effect in values:
        if (effect.layer is not Layer.TYPE or effect.applies.types_all!=('land',) or not effect.applies.subtypes_any
                or not set(effect.applies.subtypes_any)<=set(BASIC_LAND_MANA)):
            result.append(effect);continue
        original=_matches(effect.applies,base,context);dependencies=set(effect.depends_on)
        for candidate in values:
            if candidate.effect_id==effect.effect_id or candidate.layer is not Layer.TYPE or candidate.sublayer!=effect.sublayer:
                continue
            if not any(op.op in {'set_basic_land_types','set_types','add_types','remove_types'} for op in candidate.operations):continue
            if not bound_to_object(candidate) or not _matches(candidate.applies,base,context):continue
            changed=deepcopy(base)
            for operation in candidate.operations:_apply_operation(changed,operation,context)
            if _matches(effect.applies,changed,context)!=original:dependencies.add(candidate.effect_id)
        result.append(replace(effect,depends_on=tuple(sorted(dependencies))))
    return tuple(result)
