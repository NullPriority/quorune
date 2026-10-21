from __future__ import annotations
from collections.abc import Mapping

"""Closed Hexproof keyword values and source-quality comparisons."""

QUALIFIED_HEXPROOF_CAPABILITY='target.protection.hexproof_qualified'
HEXPROOF_COLORS={'white':'W','blue':'U','black':'B','red':'R','green':'G'}
HEXPROOF_TYPES={'artifacts':'artifact','battles':'battle','creatures':'creature','enchantments':'enchantment',
    'instants':'instant','lands':'land','planeswalkers':'planeswalker','sorceries':'sorcery'}
QUALIFIED_HEXPROOF_KEYWORDS=frozenset('hexproof from '+value for value in (*HEXPROOF_COLORS,*HEXPROOF_TYPES,'monocolored','multicolored'))


def keyword_type_terms(keywords):
    values={str(value).casefold() for value in keywords}
    return values|({'hexproof'} if values.intersection(QUALIFIED_HEXPROOF_KEYWORDS) else set())


def keyword_is_removed(actual,requested):
    actual=str(actual).casefold();requested=str(requested).casefold()
    return actual==requested or requested=='hexproof' and actual in QUALIFIED_HEXPROOF_KEYWORDS


def qualified_hexproof_prohibits(keywords,source):
    variants=set(keywords).intersection(QUALIFIED_HEXPROOF_KEYWORDS)
    if not variants:return False
    if source is None:return True
    for keyword in variants:
        quality=keyword.removeprefix('hexproof from ')
        if quality in HEXPROOF_TYPES:
            if source.card_types is None or HEXPROOF_TYPES[quality] in source.card_types:return True
        elif source.colors is None:return True
        elif quality in HEXPROOF_COLORS:
            if HEXPROOF_COLORS[quality] in source.colors:return True
        elif quality=='monocolored' and len(source.colors)==1:return True
        elif quality=='multicolored' and len(source.colors)>=2:return True
    return False


def qualified_hexproof_runtime_dependencies(program):
    def contains(value):
        if isinstance(value,str):return value.casefold() in QUALIFIED_HEXPROOF_KEYWORDS
        if isinstance(value,Mapping):return any(contains(v) for v in value.values())
        return isinstance(value,(tuple,list)) and any(contains(v) for v in value)
    return (QUALIFIED_HEXPROOF_CAPABILITY,) if contains((program.coverage,program.handlers,program.effects,program.target_schema)) else ()
