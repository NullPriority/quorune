"""A fixed source counter prefix before one independently read scalar result."""
import re
from .closed_effect_programs import ClosedEffectProgramTemplate,_candidate_partitions
from .counter_placement_templates import fixed_counter_placement_effect_template
from .scalar_effect_amounts import scalar_effect_amount_template
from ..scalar_effect_amount_model import scalar_amount_specs,ScalarAmountOrigin


def source_counter_quantity_sequence(text,*,card_name,compile_fixed,allow_pronoun=False,event=None):
    for clauses in _candidate_partitions(text.strip()):
        if len(clauses)!=2:continue
        first=clauses[0]
        if allow_pronoun:first=re.sub(r'(?<= on) (?:it|him|her)(?=\.)',' this permanent',first,flags=re.I)
        counter=fixed_counter_placement_effect_template(first,card_name=card_name)
        if counter is None:continue
        prefix=counter.compiled()
        if prefix[2] is not None or len(prefix[1])!=1 or prefix[1][0].get('card')!='$source.zone_object':continue
        body=re.sub(r'^Then\s+','',clauses[1],flags=re.I)
        body=re.sub(r'(?<= counters on) (?:it|him|her)(?=\.)',' this permanent',body,flags=re.I)
        body=re.sub(r'(?<= counter on) (?:it|him|her)(?=\.)',' this permanent',body,flags=re.I)
        scalar=scalar_effect_amount_template(body,source_name=card_name,event=event,source_event=allow_pronoun,compile_fixed=compile_fixed)
        if scalar is None or scalar[2] is not None or len(scalar[1])!=1:continue
        if any(spec.origin is not ScalarAmountOrigin.SOURCE for spec in scalar_amount_specs(scalar[1])):continue
        if scalar[1][0].get('op') not in {'draw','create_token','life','lose_life','damage','damage_fixed_set'}:continue
        return ClosedEffectProgramTemplate(component_template_ids=(prefix[0],scalar[0]),component_effect_counts=(1,1),
            _effects=(*prefix[1],*scalar[1]),_target_schema=None,
            mechanic_ids=tuple(dict.fromkeys(('closed-effect-program',*prefix[3],*scalar[3])))).compiled()
    return None


def source_bound_sequence_result(binding,body,*,card_name,effect_template):
    from .source_self_effect_templates import source_declaration_sequence
    declarations = source_declaration_sequence(body, card_name=card_name, compile_effect=effect_template)
    if declarations is not None:return declarations,True
    quantity=source_counter_quantity_sequence(body,card_name=card_name,
        compile_fixed=lambda text:effect_template(text,card_name=card_name),
        allow_pronoun=binding.event.value.endswith('.self') or binding.variant in {'source_attacks','this_creature_blocks','this_creature_becomes_blocked'},event=binding.event.value)
    return (quantity,False) if quantity is not None else None
