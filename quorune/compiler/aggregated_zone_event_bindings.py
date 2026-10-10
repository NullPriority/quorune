from __future__ import annotations

"""One-or-more public zone predicates over the existing sealed occurrence owner."""

from dataclasses import replace
import re

from .creature_subtypes import canonical_creature_subtype_surface
from .qualified_zone_event_bindings import qualified_public_zone_event_binding_spec

AGGREGATED_ZONE_VARIANT = 'one_or_more_qualified_public_zone_query'
_PATTERN = re.compile(r'(?:When|Whenever) one or more (?P<subject>.+?) (?P<verb>enter|die|leave the battlefield), (?P<body>.+)',re.I)
_TYPES = {'artifacts':'artifact','creatures':'creature','enchantments':'enchantment','lands':'land','permanents':'permanent','tokens':'token'}


def aggregated_public_zone_event_binding_spec(text: str, *, card_name: str | None = None):
    match=_PATTERN.fullmatch(text)
    if match is None:return None
    if re.search(r'\b(?:them|they|those|that (?:creature|artifact|permanent|card))\b',match['body'],re.I):return None
    subject=match['subject']
    subject=re.sub(r'\b(artifacts|creatures|enchantments|lands|permanents|tokens)\b',lambda m:_TYPES[m[0].casefold()],subject,flags=re.I)
    head,*tail=subject.split(' ',1)
    another=head.casefold()=='other'
    if another:
        subject=tail[0] if tail else '';head,*tail=subject.split(' ',1)
    singular=_TYPES.get(head.casefold()) or canonical_creature_subtype_surface(head) or (
        head if head.casefold() in {*_TYPES.values(),'legendary','snow','basic','nontoken','token','white','blue','black','red','green'} else None)
    if singular is None:return None
    subject=singular+(' '+tail[0] if tail else '')
    article='another ' if another else 'an ' if singular.startswith(('artifact','enchantment')) else 'a '
    verb={'enter':'enters','die':'dies','leave the battlefield':'leaves the battlefield'}[match['verb'].casefold()]
    compiled=qualified_public_zone_event_binding_spec(f"Whenever {article}{subject} {verb}, {match['body']}",card_name=card_name)
    return replace(compiled,variant=AGGREGATED_ZONE_VARIANT) if compiled else None
