from __future__ import annotations

"""One fixed destruction instruction through the current attachment owner."""

from dataclasses import dataclass
import re
from ..attachment_references import AttachmentReferenceKind,AttachmentReferenceSpec


@dataclass(frozen=True,slots=True)
class AttachedDestructionTemplate:
    reference: AttachmentReferenceSpec

    def compiled(self):
        return ('destroy-attached-permanent-v1',({'op':'destroy','card':self.reference.to_dict()},),None,('destroy',))


def attached_destruction_template(text,*,source_attachment_relation):
    match=re.fullmatch(r'Destroy (?P<relation>enchanted|equipped|fortified) (?P<type>artifact|creature|enchantment|land|permanent|planeswalker|battle)\.',text,re.I)
    if match is None:return None
    relation={'enchanted':AttachmentReferenceKind.ENCHANTED,'equipped':AttachmentReferenceKind.EQUIPPED,
        'fortified':AttachmentReferenceKind.FORTIFIED}[match['relation'].casefold()]
    if source_attachment_relation is not relation:return None
    return AttachedDestructionTemplate(AttachmentReferenceSpec(relation,match['type'].casefold()))
