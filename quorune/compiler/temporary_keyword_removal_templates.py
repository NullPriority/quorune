from __future__ import annotations

"""Complete fixed EOT removal grammar over existing source/target/query owners."""

import re

from ..rules.temporary_keyword_removal import OPERATION,MECHANIC
from .fixed_target_effect_sequences import _keyword_list
from .fixed_resolution_characteristics import _subject


def temporary_keyword_removal_template(text,*,source_name,source_is_permanent,source_card_types):
    normalized=re.sub(r'\s+\([^()]*\)\.?$','',text.strip())
    prefix=re.fullmatch(r'Until end of turn, (?P<body>.+?)\.?',normalized,re.I)
    suffix=re.fullmatch(r'(?P<body>.+?) until end of turn\.?',normalized,re.I)
    match=prefix or suffix
    if match is None:return None
    body=match['body'].rstrip('.')
    parsed=re.fullmatch(r'(?P<subject>.+?) (?:(?:gets? (?P<p>[+-]\d+)/(?P<t>[+-]\d+) and )|(?:gains? (?P<before>.+?) and ))?loses? (?P<remove>.+?)(?: and gains? (?P<after>.+))?',body,re.I)
    if parsed is None or parsed['before'] and parsed['after']:return None
    removed=_keyword_list(parsed['remove'],extended=True)
    added=_keyword_list(parsed['before'] or parsed['after'],extended=True) if parsed['before'] or parsed['after'] else ()
    if removed is None or added is None or not removed or set(removed).intersection(added):return None
    selected=_subject(parsed['subject'],source_name=source_name,source_is_permanent=source_is_permanent,source_card_types=source_card_types)
    if selected is None:return None
    selection,schema=selected
    if 'predicate' in selection and 'creature' not in selection['predicate'].get('types_all',()):return None
    effect={'op':OPERATION,'schema_version':11,**selection,'power':int(parsed['p'] or 0),'toughness':int(parsed['t'] or 0),
        'remove_keywords':list(removed),'keywords':list(added)}
    mechanics=('cr-611-continuous-effects',MECHANIC,*(keyword.casefold() for keyword in (*removed,*added)),
        *(('cr-115-targets',) if schema is not None else ()))
    return 'fixed-temporary-keyword-removal-v1',(effect,),schema,tuple(dict.fromkeys(mechanics))
