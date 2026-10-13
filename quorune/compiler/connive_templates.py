from __future__ import annotations

"""Full-consumption fixed Connive grammar with exact recipient bindings."""

import re
from ..connive import FixedConniveSpec, connive_effect, CONNIVE_MECHANIC
from ..rules.source_references import SourceReferenceSpec
from .fixed_numbers import fixed_number


def fixed_connive_effect_template(text: str, *, source_name: str, source_is_permanent: bool | None = None,
                                 allow_source_pronoun: bool = False):
    match=re.fullmatch(r'(?P<subject>.+?) connives?(?: (?P<count>[0-9]+|one|two|three|four|five))?\.?',text.strip(),re.I)
    if match is None:return None
    subject=match['subject'];schema=None
    if subject.casefold() in {'it','he','she'} and not allow_source_pronoun:return None
    if subject.casefold() in {'this creature','this permanent','it','he','she'} or SourceReferenceSpec(source_name).matches(subject):
        if source_is_permanent is not True:return None
        card='$source';player='$source.connive_controller'
    elif subject.casefold() in {'target creature','target creature you control'}:
        card='$target.0';player='$target.current_controller.0'
        schema={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':1}
        if subject.casefold().endswith('you control'):schema['controller_relation']='you'
    else:return None
    try:spec=FixedConniveSpec(fixed_number(match['count']) if match['count'] else 1)
    except ValueError:return None
    return ('fixed-connive-v1',(connive_effect(spec,card=card,player=player),),schema,
            (CONNIVE_MECHANIC, 'cr-115-targets') if schema is not None else (CONNIVE_MECHANIC,))
