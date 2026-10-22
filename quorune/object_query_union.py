from __future__ import annotations

from dataclasses import fields, replace
from typing import TYPE_CHECKING
from collections.abc import Mapping

if TYPE_CHECKING:
    from .object_predicate import ObjectQuerySpec


RECEIVER_UNION_CAPABILITY = 'state_query.permanent.receiver_union'


def validate_receiver_union_wire(value: Mapping) -> None:
    """Reject nondefault reserved data before legacy version promotion."""
    from .object_predicate import ObjectQueryError, ObjectQuerySpec
    defaults = ObjectQuerySpec(_serialization_version=9).to_dict()
    shared = {'zones', 'owner', 'controller', 'excluded_controllers', 'exclude_ref', 'any_of'}
    leaf = {'zones', 'types_all', 'subtypes_all', 'supertypes_all', 'colors_all', 'exclude_ref'}
    for part, allowed in ((value, shared), *((part, leaf) for part in value['any_of'])):
        if not isinstance(part, Mapping):
            raise ObjectQueryError('Receiver union wire branches must be objects')
        for name, item in part.items():
            if name in allowed:
                continue
            if name not in defaults or item != defaults[name]:
                raise ObjectQueryError('Receiver union wire contains an unowned predicate')


def validate_receiver_union(query: ObjectQuerySpec) -> tuple[ObjectQuerySpec, ...]:
    """Keep a flat union within public, earlier-layer battlefield facts."""
    from .object_predicate import ObjectQueryError, ObjectQuerySpec

    branches = query.any_of
    if not isinstance(branches, (list, tuple)) or not 2 <= len(branches) <= 4:
        raise ObjectQueryError('Receiver unions require two through four flat branches')
    shared = {'zones', 'owner', 'controller', 'excluded_controllers', 'exclude_ref'}
    leaf = {'zones', 'types_all', 'subtypes_all', 'supertypes_all', 'colors_all', 'exclude_ref'}
    empty = ObjectQuerySpec()
    for value, allowed in ((query, shared | {'any_of'}), *((part, leaf) for part in branches)):
        if not isinstance(value, ObjectQuerySpec):
            raise ObjectQueryError('Receiver union branches must be typed queries')
        if value.zones != ('battlefield',):
            raise ObjectQueryError('Receiver unions require battlefield queries')
        for item in fields(value):
            if item.name.startswith('_') or item.name in allowed:
                continue
            if getattr(value, item.name) != getattr(empty, item.name):
                raise ObjectQueryError('Receiver unions only support fixed public characteristic leaves')
    for part in branches:
        if not any((part.types_all, part.subtypes_all, part.supertypes_all, part.colors_all)):
            raise ObjectQueryError('Receiver union branches must select a characteristic')
        if part.supertypes_all not in {(), ('snow',)}:
            raise ObjectQueryError('Receiver union supertype leaves only support snow')
    if len(set(branches)) != len(branches):
        raise ObjectQueryError('Receiver unions require distinct branches')
    return tuple(branches)


def bind_receiver_union_source(query: ObjectQuerySpec, source_ref: str) -> ObjectQuerySpec:
    """Resolve branch-local other at the existing continuous source boundary."""
    if not query.any_of:
        return query
    return replace(query, any_of=tuple(
        replace(part, exclude_ref=source_ref if part.exclude_ref == '$source' else part.exclude_ref)
        for part in query.any_of
    ))


def validate_receiver_union_template(query: ObjectQuerySpec) -> None:
    from .object_predicate import ObjectQueryError
    if any(part.exclude_ref not in {None, '$source'} for part in query.any_of):
        raise ObjectQueryError('Receiver union templates reserve branch exclusion for the source')
