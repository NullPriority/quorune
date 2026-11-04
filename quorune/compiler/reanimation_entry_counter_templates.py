"""Compose fixed entry counters with the existing exact targeted return."""
from dataclasses import dataclass
import re
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .reanimation_templates import FixedTargetReanimationTemplate,fixed_target_reanimation_effect_template
from ..reanimation_entry_counters import (REANIMATION_ENTRY_COUNTER_MECHANIC,
    fixed_reanimation_entry_counter)
from ..keyword_counters import keyword_counter_mechanic


@dataclass(frozen=True,slots=True)
class FixedReanimationEntryCounterTemplate:
    base: FixedTargetReanimationTemplate
    counter_name: str
    amount: int

    def __post_init__(self):
        if not isinstance(self.base,FixedTargetReanimationTemplate):
            raise ValueError('Entry-counter reanimation requires its typed scalar base')
        fixed_reanimation_entry_counter({'counter_name':self.counter_name,'amount':self.amount})

    def compiled(self):
        template,effects,schema,mechanics=self.base.compiled()
        counter={'counter_name':self.counter_name,'amount':self.amount}
        effect={**effects[0],'schema_version':3,'entry_counter':counter,'source':'$stack'}
        keyword=keyword_counter_mechanic(self.counter_name)
        return (template+'-entry-'+self.counter_name.replace(' ','-')+'-'+str(self.amount)+'-v3',
            (effect,),schema,tuple(dict.fromkeys((*mechanics,REANIMATION_ENTRY_COUNTER_MECHANIC,*((keyword,) if keyword else ())))))


def fixed_reanimation_entry_counter_template(text):
    match=re.fullmatch(rf'(?P<base>.+? to|.+? onto) the battlefield(?P<tapped> tapped)?'
        rf'(?P<control> under (?:your|its owner[\'’]s) control)? with '
        rf'(?P<count>an|{FIXED_COUNT_PATTERN}) (?:additional )?(?P<name>.+?) counter(?P<plural>s)? on it\.?',text.strip(),re.I)
    if match is None:return None
    base=fixed_target_reanimation_effect_template(match['base']+' the battlefield'+(match['tapped'] or '')+(match['control'] or '')+'.')
    if base is None:return None
    name=' '.join(match['name'].casefold().split());amount=fixed_number(match['count'])
    if (amount==1)==bool(match['plural']):return None
    try:return FixedReanimationEntryCounterTemplate(base,name,amount)
    except ValueError:return None
