from __future__ import annotations

"""Versioned destruction sets over current public attachment relationships."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from .affected_permanents import AffectedPermanentSetError, AffectedPermanentSetSpec
from .attachments import attachment_target_ref
from .object_predicate import ObjectQuerySpec
from .object_query import ObjectQueryResult

ATTACHED_DESTRUCTION_KIND = 'attached_permanent_destruction'
ATTACHED_DESTRUCTION_CAPABILITY = 'permanent.destroy.attached_set'


class AttachedDestructionScope(StrEnum):
    CREATURES_AND_ATTACHED = 'creatures_and_attached'
    AURAS_EQUIPMENT_ON_CREATURE = 'auras_equipment_on_creature'
    CURSES_ON_ACTOR = 'curses_on_actor'
    AURAS_ON_LAND = 'auras_on_land'


_TARGET_SCOPES = frozenset({
    AttachedDestructionScope.AURAS_EQUIPMENT_ON_CREATURE,
    AttachedDestructionScope.AURAS_ON_LAND,
})


@dataclass(frozen=True, slots=True, init=False)
class AttachmentDestructionSetSpec(AffectedPermanentSetSpec):
    """An explicitly tagged new set; historical base-set layouts stay intact."""

    scope: AttachedDestructionScope
    target_ref: str | None
    schema_version: int

    def __init__(self, scope: AttachedDestructionScope, target_ref: str | None = None,
                 schema_version: int = 1):
        if (type(schema_version) is not int or schema_version != 1
                or not isinstance(scope, AttachedDestructionScope)):
            raise AffectedPermanentSetError('Attached destruction requires one version1 scope')
        targeted = scope in _TARGET_SCOPES
        if targeted:
            if type(target_ref) is not str or not target_ref:
                raise AffectedPermanentSetError('Attached destruction target reference is required')
        elif target_ref is not None:
            raise AffectedPermanentSetError('Untargeted attached destruction cannot accept a target')
        AffectedPermanentSetSpec.__init__(self, query=ObjectQuerySpec(zones=('battlefield',)))
        object.__setattr__(self, 'scope', scope)
        object.__setattr__(self, 'target_ref', target_ref)
        object.__setattr__(self, 'schema_version', schema_version)

    @property
    def targeted(self):
        return self.scope in _TARGET_SCOPES

    def to_dict(self):
        return {'selection_kind': ATTACHED_DESTRUCTION_KIND, 'schema_version': 1,
                'scope': self.scope.value, 'target_ref': self.target_ref}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {'selection_kind', 'schema_version', 'scope', 'target_ref'}
                or value['selection_kind'] != ATTACHED_DESTRUCTION_KIND):
            raise AffectedPermanentSetError('Attached destruction set fields are incomplete or unknown')
        try:
            return cls(AttachedDestructionScope(value['scope']), value['target_ref'], value['schema_version'])
        except (TypeError, ValueError) as exc:
            raise AffectedPermanentSetError('Attached destruction set is malformed') from exc


def _public_attachment_row_card(host, row: ObjectQueryResult):
    card = host.state.cards.get(row.object_id)
    if (card is None or card.logical_object_id != row.logical_object_id
            or card.ref != row.ref or card.zone != 'battlefield'):
        raise AffectedPermanentSetError('Attachment projection requires its current public incarnation')
    return card


def current_attachment_destruction_rows(host, rows: Sequence[ObjectQueryResult]):
    """Use reciprocal attachment edges for attachment-set selection."""
    result = []
    for row in rows:
        if row.zone != 'battlefield' or row.phased_out:
            continue
        card = _public_attachment_row_card(host, row)
        result.append(replace(row, attached_to_ref=attachment_target_ref(
            host.state.cards, host.state.players, card)))
    return tuple(result)


def uses_enchanted_permanent_state(query: ObjectQuerySpec) -> bool:
    predicate = query.state_predicate
    return ((predicate is not None and predicate.enchanted is not None)
            or any(uses_enchanted_permanent_state(branch) for branch in query.any_of))


def current_enchanted_destruction_rows(host, rows: Sequence[ObjectQueryResult]):
    """Project the existing effective Aura characterization onto live public rows."""
    result = []
    for row in rows:
        if row.zone != 'battlefield' or row.phased_out:
            continue
        card = _public_attachment_row_card(host, row)
        enchanted, _, _ = host._attachment_public_state(card)
        result.append(replace(row, enchanted=enchanted))
    return tuple(result)


def select_attachment_destruction_rows(rows: Sequence[ObjectQueryResult],
        spec: AttachmentDestructionSetSpec, *, actor: str):
    """Select once from immutable effective rows, without target legality or writes."""
    if not isinstance(spec, AttachmentDestructionSetSpec) or type(actor) is not str or not actor:
        raise AffectedPermanentSetError('Attached destruction selection requires typed scope and actor')
    public = tuple(row for row in rows if row.zone == 'battlefield' and not row.phased_out)
    scope = spec.scope
    if scope is AttachedDestructionScope.CREATURES_AND_ATTACHED:
        anchors = frozenset(row.ref for row in public if 'creature' in row.types)
        return tuple(row for row in public if row.ref in anchors or row.attached_to_ref in anchors)
    if scope is AttachedDestructionScope.CURSES_ON_ACTOR:
        return tuple(row for row in public if 'curse' in row.subtypes and row.attached_to_ref == actor)
    required_type = 'land' if scope is AttachedDestructionScope.AURAS_ON_LAND else 'creature'
    if not any(row.ref == spec.target_ref and required_type in row.types for row in public):
        raise AffectedPermanentSetError('Attached destruction requires its current legal anchor')
    wanted = {'aura'} if required_type == 'land' else {'aura', 'equipment'}
    return tuple(row for row in public
                 if row.attached_to_ref == spec.target_ref and wanted.intersection(row.subtypes))
