"""Compose existing fixed targeted damage with its simultaneous controller share."""
from dataclasses import dataclass
from . import regex_working_set as re

from ..fixed_dual_damage_model import FixedTargetControllerDamageSpec,FIXED_DUAL_DAMAGE_FIELD,FIXED_DUAL_DAMAGE_MECHANIC
from .damage_templates import FixedDamageEffectTemplate,fixed_damage_effect_template,source_pronoun_damage_effect_template


@dataclass(frozen=True,slots=True)
class FixedDualDamageTemplate:
    target: FixedDamageEffectTemplate
    spec: FixedTargetControllerDamageSpec

    def compiled(self):
        return ('damage-fixed-target-controller-v1',({'op':'damage_fixed_set','source':'$source',
            'amount':self.spec.total,FIXED_DUAL_DAMAGE_FIELD:self.spec.to_dict()},),
            self.target.target_schema,(*self.target.mechanics,FIXED_DUAL_DAMAGE_MECHANIC))


def fixed_dual_damage_template(text,*,card_name,source_is_permanent):
    match = re.fullmatch(r'(?P<target>.+? deals [1-9][0-9]* damage to .+?) and '
        r'(?P<controller>[1-9][0-9]*) damage to you\.', ' '.join(text.strip().split()),re.I)
    if match is None:return None
    targeted = fixed_damage_effect_template(match['target']+'.',card_name=card_name)
    if targeted is None and source_is_permanent is True:
        targeted = source_pronoun_damage_effect_template(match['target']+'.')
    if (not isinstance(targeted,FixedDamageEffectTemplate) or targeted.target_schema is None
            or targeted.effects[0].get('op')!='damage' or targeted.effects[0].get('target')!='$target.0'):
        return None
    return FixedDualDamageTemplate(targeted,FixedTargetControllerDamageSpec(targeted.amount,int(match['controller'])))
