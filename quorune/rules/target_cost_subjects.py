from __future__ import annotations

"""Read-only current public targets for an immutable cost subject."""

from dataclasses import replace

from ..object_query import ObjectQueryResult, object_matches_query
from ..target_cost_subject import TargetCostSubjectSpec


def target_cost_subject_refs(spec: TargetCostSubjectSpec, *, source_ref, controller,
                             attached_ref=None, objects=None):
    if not isinstance(spec, TargetCostSubjectSpec):
        raise ValueError('Target-cost subject must be typed')
    if any(type(value) is not str or not value for value in (source_ref, controller)):
        raise ValueError('Target-cost subject requires current provider identities')
    if attached_ref is not None and (type(attached_ref) is not str or not attached_ref):
        raise ValueError('Target-cost attachment reference must be current or absent')
    if spec.relation == 'source':
        return (source_ref,)
    if spec.relation == 'attached':
        return () if attached_ref is None else (attached_ref,)
    if not isinstance(objects, tuple) or any(not isinstance(row, ObjectQueryResult) for row in objects):
        raise ValueError('Target-cost query requires an immutable public snapshot')
    query = replace(spec.predicate,
        controller=controller if spec.controller_relation == 'source_controller' else None)
    return tuple(sorted({row.ref for row in objects if object_matches_query(row, query)}))
