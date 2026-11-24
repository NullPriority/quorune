from __future__ import annotations

"""Complete fixed EOT removal grammar over existing source/target/query owners."""

from . import regex_working_set as re

from ..rules.temporary_keyword_removal import (
    ALL_LANDWALK_ABILITIES, CHOICE_FIELD, CHOICE_OPTION_SETS, CHOICE_PROMPT, OPERATION, MECHANIC, PROTECTION_FROM_BLACK,
    opponent_permanent_keyword_removal_query,
)
from .fixed_target_effect_sequences import _keyword_list
from .fixed_resolution_characteristics import _subject


_TYPED_TARGET_REMOVALS = {
    'target creature loses protection from black until end of turn': PROTECTION_FROM_BLACK,
    'target creature loses all landwalk abilities until end of turn': ALL_LANDWALK_ABILITIES,
}


def temporary_keyword_removal_template(text,*,source_name,source_is_permanent,source_card_types):
    normalized=re.sub(r'\s+\([^()]*\)\.?$','',text.strip())
    prefix=re.fullmatch(r'Until end of turn, (?P<body>.+?)\.?',normalized,re.I)
    suffix=re.fullmatch(r'(?P<body>.+?) until end of turn\.?',normalized,re.I)
    match=prefix or suffix
    if match is None:return None
    body=match['body'].rstrip('.')
    parsed=re.fullmatch(r'(?P<subject>.+?) (?:(?:gets? (?P<p>[+-]\d+)/(?P<t>[+-]\d+) and )|(?:gains? (?P<before>.+?) and ))?loses? (?P<remove>.+?)(?: and gains? (?P<after>.+))?',body,re.I)
    if parsed is None or parsed['before'] and parsed['after']:return None
    typed_removal = _TYPED_TARGET_REMOVALS.get(normalized.rstrip('.').casefold())
    removed = (typed_removal,) if typed_removal else _keyword_list(parsed['remove'],extended=True)
    added=_keyword_list(parsed['before'] or parsed['after'],extended=True) if parsed['before'] or parsed['after'] else ()
    if removed is None or added is None or not removed or set(removed).intersection(added):return None
    if body.casefold() == "permanents your opponents control lose hexproof and indestructible":
        selection, schema = {"predicate": opponent_permanent_keyword_removal_query().to_dict()}, None
    else:
        selected = _subject(parsed['subject'], source_name=source_name, source_is_permanent=source_is_permanent,
                            source_card_types=source_card_types)
        if selected is None:
            return None
        selection, schema = selected
        if 'predicate' in selection and 'creature' not in selection['predicate'].get('types_all', ()):
            return None
    effect={'op':OPERATION,'schema_version':11,**selection,'power':int(parsed['p'] or 0),'toughness':int(parsed['t'] or 0),
        'remove_keywords':list(removed),'keywords':list(added)}
    mechanics=('cr-611-continuous-effects',MECHANIC,*(keyword.casefold() for keyword in (*removed,*added) if keyword != typed_removal),
        *(('cr-115-targets',) if schema is not None else ()))
    return 'fixed-temporary-keyword-removal-v1',(effect,),schema,tuple(dict.fromkeys(mechanics))


def chosen_temporary_keyword_removal_template(ability, *, text, source_name):
    if ability.zones != ('battlefield',) or not ability.tap_source or ability.cost_text.strip() != '{T}':
        return None
    body = " ".join(text.casefold().split()).removesuffix(".")
    choices = {
        "target creature loses your choice of flying, first strike, or trample until end of turn": CHOICE_OPTION_SETS[0],
        "target creature loses first strike or swampwalk until end of turn": CHOICE_OPTION_SETS[1],
    }
    words = choices.get(body)
    if words is None:
        return None
    branches = {
        word: temporary_keyword_removal_template(
            f"Target creature loses {word} until end of turn.",
            source_name=source_name, source_is_permanent=True, source_card_types=(),
        )
        for word in words
    }
    if any(branch is None for branch in branches.values()):
        return None
    effect = {
        "op": "choose_option", CHOICE_FIELD: 1, "player": "$controller", "prompt": CHOICE_PROMPT,
        "options": [{"id": word, "label": word} for word in words],
        "then_by_choice": {word: list(branches[word][1]) for word in words},
    }
    mechanics = tuple(dict.fromkeys(tag for branch in branches.values() for tag in branch[3]))
    return "chosen-temporary-keyword-removal-v1", (effect,), branches[words[0]][2], mechanics
