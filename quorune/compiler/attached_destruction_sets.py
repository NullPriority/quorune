from __future__ import annotations

"""Exact attachment-set destruction grammar through the existing destroy_all."""

from dataclasses import dataclass
from ..attachment_destruction_model import AttachmentDestructionSetSpec, AttachedDestructionScope
from .direct_target import direct_permanent_target_spec

_TEXTS = {
    'destroy all creatures and all permanents attached to creatures.': (AttachedDestructionScope.CREATURES_AND_ATTACHED, None),
    'destroy all auras and equipment attached to target creature.': (AttachedDestructionScope.AURAS_EQUIPMENT_ON_CREATURE, 'creature'),
    'destroy all curses attached to you.': (AttachedDestructionScope.CURSES_ON_ACTOR, None),
    'destroy all auras attached to target land.': (AttachedDestructionScope.AURAS_ON_LAND, 'land'),
}


@dataclass(frozen=True, slots=True)
class AttachedDestructionEffectTemplate:
    spec: AttachmentDestructionSetSpec

    def compiled(self):
        schema = None
        if self.spec.targeted:
            kind = 'land' if self.spec.scope is AttachedDestructionScope.AURAS_ON_LAND else 'creature'
            schema = direct_permanent_target_spec('target '+kind).to_target_schema()
        return ('destroy-attached-set-'+self.spec.scope.value+'-v1',
                ({'op':'destroy_all','source':'$source','set':self.spec.to_dict()},),
                schema, ('destroy','destroy-fixed-set','attached-destruction-set',
                         *(('cr-115-targets',) if schema is not None else ())))


def attached_destruction_set_template(text):
    parsed = _TEXTS.get(' '.join(text.casefold().split()))
    if parsed is None:
        return None
    scope, kind = parsed
    return AttachedDestructionEffectTemplate(AttachmentDestructionSetSpec(
        scope, '$target.0' if kind is not None else None))
