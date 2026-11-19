"""Complete paid-Surge spell and self-entry results through existing owners."""
from copy import deepcopy
from . import regex_working_set as re
from ..paid_alternative_cast_model import (
    PAID_ALTERNATIVE_CAST_CAPABILITY as CAP,PAID_ALTERNATIVE_CAST_MECHANIC as MECHANIC,
    PAID_ALTERNATIVE_RESULT_VERSION as VERSION,PAID_ALTERNATIVE_ENTRY_CONDITION,PaidAlternativeCastRequirement,
)


def paid_alternative_spell_result_template(text,*,compile_component):
    match=re.fullmatch(r"(?P<prefix>.+?\. )?If this spell's surge cost was paid, (?P<body>.+)",text.strip(),re.I)
    if match is None or re.search(r'\b(?:instead|additional|otherwise|if)\b',match['body'],re.I):return None
    result=compile_component(match['body'])
    prefix=compile_component(match['prefix'].strip()) if match['prefix'] else (None,(),None,())
    if (result[0] is None or not result[1] or result[2] is not None or prefix[2] is not None
            or match['prefix'] and prefix[0] is None or len(prefix[1])+1>8):return None
    wrapper={'op':'apply_if_public_condition','schema_version':VERSION,'player':'$controller',
        'condition':PaidAlternativeCastRequirement('surge').to_dict(),
        'cast_fact':'$context.paid_alternative_cast','effects':deepcopy(list(result[1])),
        'mechanic_ids':list(result[3]),'prefix_mechanic_ids':list(prefix[3])}
    from ..rules.paid_alternative_cast_facts import validate_paid_alternative_instruction
    try:validate_paid_alternative_instruction(wrapper)
    except (ValueError,TypeError,KeyError):return None
    from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC
    return ('fixed-paid-alternative-spell-result-v1',(*prefix[1],wrapper),None,
        tuple(dict.fromkeys((MECHANIC,RESOLUTION_CONDITION_MECHANIC,*prefix[3],*result[3]))))


def paid_alternative_entry_trigger_node(*,node_id,line,material_line,span,card_name,effect_template,
        trusted_mechanics,capability_registry,capability_profile,residuals):
    match=re.fullmatch(r'When (?P<source>.+?) enters, if its surge cost was paid, (?P<body>.+)',material_line.strip(),re.I)
    if match is None:return None
    from ..rules.source_references import SourceReferenceSpec,source_self_permanent_type
    if source_self_permanent_type(match['source']) is None and not SourceReferenceSpec(card_name).matches(match['source']):return None
    body=match['body']
    if re.match(r'it (?:deals|gets|gains) ',body,re.I):body=re.sub(r'^it ',card_name+' ',body,flags=re.I)
    from .fixed_public_characteristic_sets import fixed_public_characteristic_set_effect_template
    compiled=fixed_public_characteristic_set_effect_template(body) or effect_template(body,card_name=card_name)
    template,effects,target_schema,result_mechanics=compiled
    if template is None:return None
    effects=tuple({**effect,'predicate':{**effect['predicate'],'exclude_ref':'$source.zone_object'}}
        if effect.get('op')=='modify_all_matching_permanents_until_end_of_turn'
        and effect.get('predicate',{}).get('exclude_ref')=='$source' else effect for effect in effects)
    from .dependency_gate import dependency_gate
    mechanics=('cr-603-handling-triggered-abilities',MECHANIC,'fixed-typed-event-effect-trigger',*result_mechanics)
    gate=dependency_gate(mechanics=mechanics,effects=effects,target_schema=target_schema,
        trusted_mechanics=trusted_mechanics,capability_registry=capability_registry,
        capability_profile=capability_profile,explicit_capabilities=(CAP,))
    from .ir_model import OracleNode,append_residual
    residual_ids=()
    if gate.blockers:
        residual_ids=(append_residual(residuals,kind='dependency_contract',text=line,span=span,
            reason='Paid alternative entry needs its sealed cast fact and complete existing result owners',blockers=gate.blockers),)
    closure=gate.closure
    return OracleNode(node_id=node_id,kind='triggered_ability',text=line,span=span,
        active_zone='battlefield',event='permanent.enter.self',event_condition={'field':PAID_ALTERNATIVE_ENTRY_CONDITION,'op':'eq','value':'surge'},
        lowerable=True,exact=not residual_ids,template_id='fixed-paid-alternative-entry-v1',effects=effects,
        target_schema=target_schema,mechanics=mechanics,residual_ids=residual_ids,capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None)
