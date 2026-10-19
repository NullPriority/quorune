from __future__ import annotations

"""Match a parsed Equip to its existing trusted source-spanned keyword owner."""

from collections.abc import Mapping

from .attachment_actions import fixed_equip_ability_spec
from ..replacement.immutable import thaw_value
from ..abilities import parse_activated_abilities
from ..util import mana_cost_to_vector


def trusted_equip_keyword_covers(ability,trusted_programs):
    if ability.builtin_semantic_key!='builtin:equip' or not ability.sorcery_speed or not ability.compiled_cost:
        return False
    # The parsed ability's original line was already stripped of reminder text
    # by preflight. Match the complete cost/restriction, not just "equip".
    spec=fixed_equip_ability_spec(ability.oracle_line)
    if spec is None or ability.target_schema is None or thaw_value(ability.target_schema)!=dict(spec.target_schema):
        return False
    mana,complex_symbols=mana_cost_to_vector(spec.cost_text)
    if complex_symbols or dict(ability.mana)!=mana or ability.cost_text!=spec.cost_text:
        return False
    canonical=parse_activated_abilities(card_name='source',oracle_text=ability.oracle_line,keywords=('Equip',))
    if len(canonical)!=1:
        return False
    expected=canonical[0]
    actual=ability.to_dict();wanted=expected.to_dict()
    for key in ('ability_id','line_index'):actual.pop(key);wanted.pop(key)
    if actual!=wanted:
        return False
    for program in trusted_programs:
        span=program.provenance.get('source_span')
        if (program.trust_level=='trusted' and spec.capability_id in program.capability_dependencies
                and 'equip' in program.coverage and program.active_zone=='battlefield'
                and program.event=='continuous' and isinstance(span,Mapping)
                and type(span.get('line')) is int and span['line']==ability.line_index+1):
            return True
    return False
