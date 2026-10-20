from __future__ import annotations

"""Immutable intrinsic entry-copy values beside the canonical copy model."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .creature_subtypes import canonical_creature_subtype
from .object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from .target_numeric import TargetNumericCharacteristic, TargetNumericComparison
from .token_copy_recipes import TokenCopyExceptionSpec
from .entry_copy_predicates import EntryCopyPredicate
from .conditional_copy_model import ConditionalCopyException

ENTRY_COPY_HANDLER = 'replacement.zone.entry-copy.v1'
ENTRY_COPY_QUERY_HANDLER = 'replacement.zone.entry-copy.v2'
ENTRY_COPY_CAPABILITY = 'zone.entry.fixed_copy'
ENTRY_COPY_QUERY_CAPABILITIES = (ENTRY_COPY_CAPABILITY, 'state_query.permanent.public_state_predicate')
ENTRY_COPY_VALUES_HANDLER = 'replacement.zone.entry-copy.v3'
ENTRY_COPY_VALUES_CAPABILITY = 'zone.entry.copy_value_exceptions'
COPIABLE_ORACLE_FIELD = '_copiable_oracle_id'
COPIABLE_FACE_FIELD = '_copiable_face_id'


def copiable_oracle_id(card: Any, characteristics: Mapping[str, Any] | None = None) -> str:
    """Resolve copied program provenance while retaining physical card identity."""
    values = characteristics
    if values is None:
        values = (getattr(card, 'annotations', {}) or {}).get('copy_overrides')
    if isinstance(values, Mapping) and COPIABLE_ORACLE_FIELD in values:
        value = values[COPIABLE_ORACLE_FIELD]
        if type(value) is not str or not value:
            raise ValueError('Copiable program provenance must be one retained identity')
        return value
    return card.oracle_id


def copied_entry_preview(card: Any, prepared: Any):
    values = prepared.event.payload.get('entry_copy')
    if values is None:
        return None
    if not isinstance(values, Mapping) or COPIABLE_ORACLE_FIELD not in values:
        raise ValueError('Prepared copy entry requires retained copiable values')
    return replace(card, annotations={**card.annotations, 'copy_overrides':dict(values)},
        active_face=values.get(COPIABLE_FACE_FIELD))


def serialize_prepared_copy_entry(prepared):
    from dataclasses import fields
    from .replacement.immutable import thaw_value
    result = {field.name: getattr(prepared, field.name) for field in fields(prepared)
        if field.name!='devour_sacrifices' or prepared.devour_sacrifices}
    if prepared.devour_sacrifices:result['devour_sacrifices']=[serialize_prepared_copy_entry(v) for v in prepared.devour_sacrifices]
    result['transition_kind'] = prepared.transition_kind.value
    result['event'] = prepared.event.to_dict()
    for field in ('effects', 'counter_events', 'keyword_grants', 'journal'):
        result[field] = [value.to_dict() for value in getattr(prepared, field)]
    return thaw_value(result)


def restore_prepared_copy_entry(value, *, revision):
    from .semantic_runtime.zone_replacement_model import PreparedZoneChange
    from .replacement_effects import ReplaceableEvent, ReplacementEffect, ReplacementSelection
    from .entry_keyword_grants import EntryKeywordGrant
    from .zone_trigger_events import ZoneTransitionKind
    from dataclasses import fields
    expected={field.name for field in fields(PreparedZoneChange) if field.name!='devour_sacrifices'}
    if not isinstance(value, Mapping) or set(value) != (expected|({'devour_sacrifices'} if 'devour_sacrifices' in value else set())):
        raise ValueError('Retained copied entry has a closed identity')
    data = dict(value)
    if 'devour_sacrifices' in data:
        if type(data['devour_sacrifices']) is not list or not data['devour_sacrifices']:raise ValueError('Prepared Devour sacrifices require a nonempty array')
        data['devour_sacrifices']=tuple(restore_prepared_copy_entry(v,revision=revision) for v in data['devour_sacrifices'])
    data['state_revision'] = revision  # Choices advance revision without moving this object.
    data['transition_kind'] = ZoneTransitionKind(data['transition_kind'])
    data['event'] = ReplaceableEvent.from_dict(data['event'])
    data['effects'] = tuple(ReplacementEffect.from_dict(x) for x in data['effects'])
    data['counter_events'] = tuple(ReplaceableEvent.from_dict(x) for x in data['counter_events'])
    data['keyword_grants'] = tuple(EntryKeywordGrant(**x) for x in data['keyword_grants'])
    data['journal'] = tuple(ReplacementSelection.from_dict(x) for x in data['journal'])
    return PreparedZoneChange(**data)


def _legacy_view_of_copy_query(query: ObjectQuerySpec) -> ObjectQuerySpec:
    """Validate only represented extensions, then retain the legacy checks."""
    if query.excluded_types and (query.excluded_types != ('land',) or query.types_all or query.types_any):
        raise ValueError('Qualified copy excludes only lands from the permanent domain')
    if query.numeric_characteristic is not None:
        numeric = query.numeric_characteristic
        if (query.types_all != ('creature',) or query.types_any or query.subtypes_all
                or numeric.characteristic is not TargetNumericCharacteristic.POWER
                or numeric.comparison is not TargetNumericComparison.AT_LEAST
                or query.state_predicate is not None or query.excluded_types):
            raise ValueError('Qualified copy uses one fixed creature-power minimum')
    if query.state_predicate is not None and (
            query.state_predicate != PermanentStatePredicateSpec(entered_this_turn=True)
            or query.types_all or query.types_any or query.excluded_types or query.subtypes_all):
        raise ValueError('Qualified copy history selects permanents that entered this turn')
    equipment = query.types_all == ('artifact',) and query.subtypes_all == ('equipment',)
    return replace(query, excluded_types=(), numeric_characteristic=None, state_predicate=None,
        subtypes_all=() if equipment else query.subtypes_all)


@dataclass(frozen=True, slots=True)
class EntryCopySpec:
    selection: ObjectQuerySpec
    exception: TokenCopyExceptionSpec = TokenCopyExceptionSpec()
    schema_version: int = 1
    predicate: EntryCopyPredicate | None = None
    conditional_exception: ConditionalCopyException | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4, 5}:
            raise ValueError('Entry copy requires one current typed schema')
        if not isinstance(self.selection, ObjectQuerySpec) or not isinstance(self.exception, TokenCopyExceptionSpec):
            raise ValueError('Entry copy requires typed selection and copy exceptions')
        if (self.schema_version==4)!=(self.predicate is not None) or self.predicate is not None and not isinstance(self.predicate,EntryCopyPredicate):raise ValueError('Entry copy predicates require version four')
        if (self.schema_version==5)!=(self.conditional_exception is not None) or self.conditional_exception is not None and not isinstance(self.conditional_exception,ConditionalCopyException):raise ValueError('Conditional exception requires version five')
        query = _legacy_view_of_copy_query(self.selection) if self.schema_version == 2 else self.selection
        if self.schema_version in {3,4} and query.zones==('graveyard',):
            if query!=ObjectQuerySpec(zones=('graveyard',),types_all=('creature',)):raise ValueError('Graveyard copy selects only public creature cards')
            query=replace(query,zones=('battlefield',))
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
                    query.supertypes_all, query.colors_all, query.colors_any, query.colors_none, query.keywords_all,
                    query.keywords_none, query.token is not None, query.tapped is not None,
                    query.commander is not None, query.colorless is not None,
                    query.minimum_color_count is not None))
                or any(canonical_creature_subtype(v) != v for v in query.subtypes_all)
                or query.subtypes_all and query.types_all != ('creature',)):
            raise ValueError('Entry copy selection is outside the closed public permanent domain')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'selection': self.selection.to_dict(),
            'exception': self.exception.to_dict(),**({'predicate':self.predicate.to_dict()} if self.predicate is not None else {}),**({'conditional_exception':self.conditional_exception.to_dict()} if self.conditional_exception is not None else {})}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'EntryCopySpec':
        if not isinstance(value, Mapping) or set(value) != ({'schema_version','selection','exception','predicate'} if value.get('schema_version')==4 else {'schema_version','selection','exception','conditional_exception'} if value.get('schema_version')==5 else {'schema_version','selection','exception'}):
            raise ValueError('Entry copy fields are closed')
        try:
            return cls(ObjectQuerySpec.from_dict(value['selection']),
                TokenCopyExceptionSpec.from_dict(value['exception']), value['schema_version'],
                EntryCopyPredicate.from_dict(value['predicate']) if value['schema_version']==4 else None,
                ConditionalCopyException.from_dict(value['conditional_exception']) if value['schema_version']==5 else None)
        except (TypeError, KeyError) as exc:
            raise ValueError('Entry copy fields are malformed') from exc
