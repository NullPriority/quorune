from __future__ import annotations

"""Compile-time binding to one independently declared public target role."""

from copy import deepcopy
import re

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .kicked_same_target_replacements import same_target_kicked_replacement
from ..rules.source_references import SourceReferenceSpec


def public_same_target_replacement(normal_text, alternative, normal, compile_component, source_name):
    schema=normal[2]
    if schema is None or type(schema.get('count')) is not int or schema['count']!=1 or len(normal[1])!=1:
        return None
    original=normal[1][0]
    if original.get('op')=='damage' and original.get('source')=='$source' and original.get('target')=='$target.0':
        pattern=rf'(?:{SourceReferenceSpec(source_name).regex_pattern}|it) deals (?P<count>{FIXED_COUNT_PATTERN}) damage(?: to (?P<reference>that creature|that permanent or player))?\.'
        match=re.fullmatch(pattern,alternative,re.I)
        if match is None:return None
        reference=(match['reference'] or '').casefold()
        if reference=='that creature' and schema.get('types_any')!=['creature']:return None
        if reference=='that permanent or player' and set(schema.get('categories',()))!={'permanent','player'}:return None
        effect=deepcopy(original);effect['amount']=fixed_number(match['count'])
        return normal[0],(effect,),deepcopy(schema),normal[3]
    if original.get('op')=='modify_stats_until_end_of_turn':
        alternative=re.sub(r'^it gets ', 'that creature gets ',alternative,flags=re.I)
        return same_target_kicked_replacement(normal_text,alternative,normal,compile_component)
    rewritten=None
    if original.get('op')=='place_counters' and original.get('card')=='$target.0':
        match=re.fullmatch(r'Put .+ on that creature\.',alternative,re.I)
        if match:rewritten=re.sub(r'on that creature\.$','on target creature.',alternative,flags=re.I)
    elif original.get('op') in {'life','draw'} and original.get('player')=='$target.0':
        match=re.fullmatch(r'Target player (?:gains|loses|draws) .+\.',normal_text,re.I)
        if match and re.match(r'^that player ',alternative,re.I):rewritten='Target player '+alternative[len('that player '):]
    elif original.get('op')=='counter_unless_pay' and original.get('stack')=='$target.0':
        match=re.fullmatch(r'Counter (?P<target>target .+? spell) unless its controller pays .+\.',normal_text,re.I)
        if match and re.match(r'^counter that spell\b',alternative,re.I):
            rewritten='Counter '+match['target']+alternative[len('Counter that spell'):]
    elif original.get('op')=='return_graveyard_card_to_owner_hand' and original.get('card')=='$target.0':
        match=re.fullmatch(r'Return (?P<role>target .+? card from your graveyard) to your hand\.',normal_text,re.I)
        prefix='Return that card from your graveyard'
        if match and alternative.casefold().startswith(prefix.casefold()):
            rewritten='Return '+match['role']+alternative[len(prefix):]
    if rewritten is None:return None
    compiled=compile_component(rewritten)
    return compiled if compiled[0] is not None and compiled[2]==schema else None
