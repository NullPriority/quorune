"""Complete literal library procedures from printed shared tails or count overrides."""
from dataclasses import dataclass
import re
from copy import deepcopy

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .public_state_queries import fixed_public_state_condition
from ..resolution_conditions import (
    RESOLUTION_CONDITION_MECHANIC, resolution_condition_is_closed, cast_fact_result_is_fixed,
)

_NUMBER = rf'(?:a|an|{FIXED_COUNT_PATTERN})'
_SEARCH_HEAD = re.compile(rf'Search your library for (?P<optional>up to )?(?P<count>{_NUMBER}) '
    r'(?:(?P<quality>.+?) )?cards?', re.I)
_LOOK_HEAD = re.compile(rf'Look at the top (?P<count>{_NUMBER}) cards? of your library', re.I)


@dataclass(frozen=True, slots=True)
class LiteralProcedureVariant:
    normal: str
    alternative: str
    condition: str

    def expanded(self):
        return self.normal + ' If ' + self.condition + ', ' + self.alternative.rstrip('.') + ' instead.'


def _literal(head, tail):
    match = _SEARCH_HEAD.fullmatch(head)
    if match is not None:
        count = fixed_number(match['count'])
        if count == 1:
            tail = re.sub(r'\b(?:them|those cards)\b', 'it', tail, flags=re.I)
        else:
            tail = re.sub(r'\b(?:it|that card)\b', 'them', tail, flags=re.I)
        return head + ', ' + tail[0].lower() + tail[1:] if tail.casefold().startswith(('reveal ', 'put ')) else head + tail
    return head + '. ' + tail if _LOOK_HEAD.fullmatch(head) else None


def literal_procedure_variant(text):
    material = ' '.join(text.strip().split())
    count_only = re.fullmatch(rf'(?P<normal>Search your library for .+\.) If (?P<condition>[^.]+), '
        rf'(?P<alternative>search your library for [^.]+) instead of (?P<old>{_NUMBER})\.', material, re.I)
    if count_only is not None:
        normal = count_only['normal'];head = _SEARCH_HEAD.match(normal)
        alternative = _SEARCH_HEAD.fullmatch(count_only['alternative'])
        if head is None or alternative is None or fixed_number(head['count']) != fixed_number(count_only['old']):return None
        tail = normal[head.end():]
        if not tail.startswith((', ', '. ')):return None
        tail = tail[2:] if tail.startswith(', ') else tail
        complete = _literal(count_only['alternative'], tail)
        return LiteralProcedureVariant(normal, complete, count_only['condition']) if complete else None
    shared = re.fullmatch(r'(?P<normal>(?:Search your library for|Look at the top) [^.]+)\. If (?P<condition>[^.]+), '
        r'(?P<alternative>(?:instead )?(?:search your library for|look at the top) [^.]+)\. (?P<tail>.+\.)', material, re.I)
    if shared is None:return None
    alternative = shared['alternative'];leading = alternative.casefold().startswith('instead ')
    trailing = alternative.casefold().endswith(' instead')
    if leading == trailing:return None
    alternative = alternative[len('instead '):] if leading else alternative[:-len(' instead')]
    if bool(_LOOK_HEAD.fullmatch(shared['normal'])) != bool(_LOOK_HEAD.fullmatch(alternative)):return None
    normal = _literal(shared['normal'], shared['tail']);other = _literal(alternative, shared['tail'])
    return LiteralProcedureVariant(normal, other, shared['condition']) if normal and other else None


def compiled_procedure_branches(variant, compile_component):
    branches = (compile_component(variant.normal), compile_component(variant.alternative))
    if any(not branch[0] or len(branch[1]) != 1 or branch[2] is not None
            or branch[1][0].get('op') not in {'search', 'fixed_library_selection'}
            or not cast_fact_result_is_fixed(branch[1], branch[3]) for branch in branches):return None
    return branches


def public_procedure_variant(text, *, source_name, compile_component):
    variant = literal_procedure_variant(text)
    if variant is None:return None
    condition = fixed_public_state_condition(variant.condition, source_name=source_name)
    if condition is None or not resolution_condition_is_closed(condition):return None
    branches = compiled_procedure_branches(variant, compile_component)
    if branches is None:return None
    normal, alternative = branches
    wrapper = {'op': 'apply_if_public_condition', 'schema_version': 3, 'player': '$controller',
        'condition': condition.to_dict(), 'effects': deepcopy(list(alternative[1])), 'mechanic_ids': list(alternative[3]),
        'otherwise_effects': deepcopy(list(normal[1])), 'otherwise_mechanic_ids': list(normal[3]), 'prefix_mechanic_ids': []}
    return 'fixed-public-self-replacement-v1', (wrapper,), None, tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, *normal[3], *alternative[3])))
