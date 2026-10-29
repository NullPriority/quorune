from __future__ import annotations

"""Complete multi-quote and ability-replacement clauses over existing grant children."""

from copy import deepcopy
import re
from ..basic_land_types import LAND_SUBTYPES
from ..object_predicate import ObjectQuerySpec
from ..semantic_runtime.composite_grants import COMPOSITE_GRANT_HANDLER,COMPOSITE_GRANT_CAPABILITY
from .continuous_templates import _attached_modifier
from .oracle_source_text import without_ability_word_label
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .static_runtime_nodes import runtime_handler_node


def _composite_shell(text):
    text=without_ability_word_label(' '.join(text.strip().split()))
    if text is None:return None
    remove=False;land_types=False
    match=re.fullmatch(r'(?P<subject>.+?) (?:has|have) (?P<quotes>.+) and (?:loses|lose) all other abilities\.?',text,re.I)
    if match is not None:remove=True
    else:
        match=re.fullmatch(r'(?P<subject>.+?) (?:loses|lose) all (?P<land>land types and )?abilities and (?:has|have) (?P<quotes>.+?)\.?',text,re.I)
        if match is not None:remove=True;land_types=bool(match['land'])
        else:match=re.fullmatch(r'(?P<subject>.+?) (?:has|have) (?P<quotes>.+?)\.?',text,re.I)
    if match is None:return None
    quoted=match['quotes'].rstrip('.')
    if re.fullmatch(r'"[^"]+"(?:\s*(?:,\s*(?:and\s+)?|and\s+)"[^"]+"){0,3}',quoted,re.I) is None:return None
    children=tuple(re.findall(r'"([^"]+)"',quoted))
    if len(children)<2 and not remove:return None
    subject=match['subject']
    attached=re.fullmatch(r'(?:Enchanted|Equipped) (?P<kind>artifact|battle|creature|enchantment|land|permanent|planeswalker)',subject,re.I)
    if attached:
        predicate=ObjectQuerySpec(zones=('battlefield',),types_all=() if attached['kind'].casefold()=='permanent' else (attached['kind'].casefold(),))
        target={'kind':'attached','controller':None,'exclude_source':False,'predicate':predicate.to_dict()}
    else:
        parsed=fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None:return None
        relation,predicate,excluded=parsed
        target={'kind':'fixed_query','controller':relation,'exclude_source':excluded,'predicate':predicate.to_dict()}
    modifier=_attached_modifier();modifier['remove_all_abilities']=remove
    if land_types:
        modifier['type_operations']=[{'op':'remove_types','field':'subtypes','values':[v.title() for v in sorted(LAND_SUBTYPES)]}]
    return target,modifier,children


def composite_granted_ability_nodes(**values):
    shell=_composite_shell(values['material_line'])
    if shell is None:return None
    target,modifier,children=shell
    from .attached_granted_ability_nodes import (
        _compile_granted_ability_pair,_EXTERNAL_ATTACHMENT_REFERENCE,
    )
    from .granted_characteristic_nodes import granted_characteristic_reference_is_closed
    fragments=[];nodes=[];required={COMPOSITE_GRANT_CAPABILITY,'continuous.characteristics.fixed_public_setting','continuous.ability.fixed_query_grant'}
    template='continuous-composite-typed-grant-v1'
    def compiled(fragment,dependencies):
        return template,{'handler_id':COMPOSITE_GRANT_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
            'target':target,'modifier':{**deepcopy(modifier),'add_ability_fragments':[dict(fragment)]}},tuple(sorted(required|set(dependencies)))
    for slot,quote in enumerate(children,1):
        if (_EXTERNAL_ATTACHMENT_REFERENCE.search(quote) and not granted_characteristic_reference_is_closed(quote)
                or values['source_name'].casefold() in quote.casefold()):return None
        arguments={k:values[k] for k in ('record','face_id','line','material_line','span','trusted_mechanics',
            'capability_registry','capability_profile','compile_inner','effect_template','trigger_effect_template','material_line_for')}
        pair=_compile_granted_ability_pair(**arguments,node_id=f"{values['node_id']}:quoted-ability:{slot}",
            quoted=quote.rstrip('.')+'.',compile_shell=compiled,runtime_coverage='composite_typed_ability_grant',
            dependency_reason='Composite grants require every independently closed child and their coupled characteristic owner',residuals=[])
        if pair is None or pair[0].residual_ids:return None
        fragments.append(pair[0].handlers[0]['modifier']['add_ability_fragments'][0]);nodes.append(pair[1]);required.update(pair[0].capability_dependencies)
    descriptor={'handler_id':COMPOSITE_GRANT_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'target':target,'modifier':{**modifier,'add_ability_fragments':fragments}}
    from ..semantic_runtime.composite_grants import CompositeAbilityGrantHandler
    try:CompositeAbilityGrantHandler().validate(descriptor)
    except (TypeError,ValueError):return None
    outer=runtime_handler_node(node_id=values['node_id'],line=values['line'],span=values['span'],
        compiled=(template,descriptor,tuple(sorted(required))),kind='static_ability',event='characteristics.evaluate',
        runtime_coverage=('composite_typed_ability_grant',),dependency_reason='Composite quoted grants require every child and their coupled owner',
        capability_registry=values['capability_registry'],capability_profile=values['capability_profile'],residuals=values['residuals'])
    return (outer,*nodes)
