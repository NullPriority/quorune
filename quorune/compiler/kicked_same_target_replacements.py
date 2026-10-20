from __future__ import annotations

"""An explicit anaphor retains one already declared public target role."""

from copy import deepcopy
import re

from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number


def same_target_kicked_replacement(normal_text,alternative,normal,compile_component):
    schema=normal[2]
    if (schema is None or type(schema.get('count')) is not int or schema['count']!=1
            or len(normal[1])!=1 or re.search(r'\btarget\b',alternative,re.I)):
        return None
    original=normal[1][0]
    if original.get('op')=='modify_stats_until_end_of_turn':
        if not re.match(r'^Target creature gets ',normal_text,re.I) or not re.match(r'^that creature gets ',alternative,re.I):
            return None
        replacement=compile_component('Target creature'+alternative[len('that creature'):])
        return replacement if (replacement[0] is not None and replacement[2]==schema
            and 1<=len(replacement[1])<=3 and replacement[1][0].get('op')==original['op']
            and all(effect.get('op')=='grant_keyword_until_end_of_turn' for effect in replacement[1][1:])
            and all(effect.get('card')=='$target.0' for effect in replacement[1])) else None
    if original.get('op')!='damage' or original.get('source')!='$source' or original.get('target')!='$target.0':
        return None
    count=re.fullmatch(rf'it deals (?P<count>{FIXED_COUNT_PATTERN}) damage(?: to (?P<reference>that creature|that permanent or player))?\.',alternative,re.I)
    if count is None:
        return None
    reference=(count['reference'] or '').casefold()
    if reference=='that creature' and schema.get('types_any')!=['creature']:
        return None
    if reference=='that permanent or player' and set(schema.get('categories',()))!={'permanent','player'}:
        return None
    effect=deepcopy(original);effect['amount']=fixed_number(count['count'])
    return normal[0],(effect,),deepcopy(schema),normal[3]
