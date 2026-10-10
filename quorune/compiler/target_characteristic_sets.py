from __future__ import annotations

import re
from ..rules.target_characteristic_sets import OPERATION
from .fixed_target_effect_sequences import fixed_target_characteristics_effect_template

_PATTERN=re.compile(r'(?P<prefix>Until end of turn, )?(?P<count>Up to (?:one|two|three|four|five|six)|One or two|Two|Three|Four|Five|Six) (?P<other>other )?target (?P<subject>.+?) (?:each )?(?P<verb>get|gets|gain|gains) (?P<body>.+)',re.I)
_NUMBERS={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6}


def target_characteristic_set_template(text):
    match=_PATTERN.fullmatch(text.strip())
    if match is None:return None
    result=match['body']
    if match['prefix']:result=result.rstrip('.')+' until end of turn.'
    verb='gets' if match['verb'].lower().startswith('get') else 'gains'
    subject=re.sub(r'\bcreatures\b','creature',match['subject'],flags=re.I)
    singular=('another ' if match['other'] else '')+'target '+subject+' '+verb+' '+result
    template=fixed_target_characteristics_effect_template(singular,extended=True)
    if template is None or template.target_schema is None or any(v is not None and type(v) is not int for v in (template.power,template.toughness)):return None
    count=match['count'].lower();maximum=2 if count=='one or two' else _NUMBERS[count.removeprefix('up to ')]
    schema=dict(template.target_schema);schema.pop('count')
    if count.startswith('up to '):schema['up_to']=maximum
    elif count=='one or two':schema.update(min=1,max=2)
    else:schema['count']=maximum
    mechanics=template.compiled()[3]
    return ('fixed-target-characteristic-set-v1',({'op':OPERATION,'schema_version':6,'cards':'$targets','maximum_targets':maximum,
        'power':template.power or 0,'toughness':template.toughness or 0,'keywords':list(template.keywords)},),schema,
        tuple(dict.fromkeys(mechanics)))
