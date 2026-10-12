from __future__ import annotations

"""Immutable intrinsic entry-copy values beside the canonical copy model."""

from dataclasses import dataclass
from typing import Any, Mapping

from .creature_subtypes import canonical_creature_subtype
from .object_predicate import ObjectQuerySpec
from .token_copy_recipes import TokenCopyExceptionSpec


@dataclass(frozen=True, slots=True)
class EntryCopySpec:
    selection: ObjectQuerySpec
    exception: TokenCopyExceptionSpec = TokenCopyExceptionSpec()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Entry copy requires one current typed schema')
        if not isinstance(self.selection, ObjectQuerySpec) or not isinstance(self.exception, TokenCopyExceptionSpec):
            raise ValueError('Entry copy requires typed selection and copy exceptions')
        query = self.selection
        permanent_types = {'artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker'}
        if (query.zones != ('battlefield',) or query.owner is not None
                or query.controller not in {None, '$controller'}
                or query.excluded_controllers not in {(), ('$controller',)}
                or query.controller is not None and query.excluded_controllers
                or not set((*query.types_all, *query.types_any)) <= permanent_types
                or query.types_all and query.types_any
                or query.include_phased_out or query.known_to_actor is not None
                or query.exclude_ref is not None or query.state_predicate is not None
                or query.numeric_characteristic is not None
                or any((query.excluded_types, query.subtypes_any, query.excluded_subtypes,
                    query.supertypes_all, query.colors_all, query.colors_any, query.keywords_all,
                    query.keywords_none, query.token is not None, query.tapped is not None,
                    query.commander is not None, query.colorless is not None,
                    query.minimum_color_count is not None))
                or any(canonical_creature_subtype(v) != v for v in query.subtypes_all)
                or query.subtypes_all and query.types_all != ('creature',)):
            raise ValueError('Entry copy selection is outside the closed public permanent domain')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'selection': self.selection.to_dict(),
            'exception': self.exception.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'EntryCopySpec':
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'selection', 'exception'}:
            raise ValueError('Entry copy fields are closed')
        try:
            return cls(ObjectQuerySpec.from_dict(value['selection']),
                TokenCopyExceptionSpec.from_dict(value['exception']), value['schema_version'])
        except (TypeError, KeyError) as exc:
            raise ValueError('Entry copy fields are malformed') from exc
