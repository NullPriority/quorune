from __future__ import annotations

"""Closed static combat-assignment grants through existing applicability."""

import re
from ..as_unblocked import AS_UNBLOCKED_CAPABILITY, AS_UNBLOCKED_HANDLER, CONDITIONAL_AS_UNBLOCKED_HANDLER, TEMPORARY_AS_UNBLOCKED_MECHANIC, AsUnblockedAssignmentPermission
from ..ability_fragments import ability_fragment_to_dict
from ..rules.source_references import SourceReferenceSpec
from .continuous_templates import fixed_query_keyword_grant_handler,attached_fixed_characteristics_handler,fixed_public_state_characteristics_handler


def static_as_unblocked_handler(text: str, *, source_name: str):
    source=rf"(?:this creature|{SourceReferenceSpec(source_name).regex_pattern})"
    match=re.fullmatch(rf"You may have {source} assign (?:its|his|her|their) combat damage as though (?:it|he|she|they) weren't blocked\.?",text.strip(),re.I)
    fragment=ability_fragment_to_dict(AsUnblockedAssignmentPermission())
    if match is not None:
        return ('static-as-unblocked-assignment-v1',{'handler_id':AS_UNBLOCKED_HANDLER,'schema_version':1,'event':'characteristics.evaluate','fragment':fragment},(AS_UNBLOCKED_CAPABILITY,))
    attached=re.fullmatch(r"Enchanted creature's controller may have it assign its combat damage as though it weren't blocked\.?",text.strip(),re.I)
    query=re.fullmatch(r"(?P<prefix>As long as .+?, )?For each (?P<subject>.+?), you may have that creature assign its combat damage as though it weren't blocked\.?",text.strip(),re.I)
    quoted=re.fullmatch(r'(?P<subject>.+?) have "You may have this creature assign its combat damage as though it weren\'t blocked\."\.?',text.strip(),re.I)
    if attached is not None:synthetic='Enchanted creature has vigilance.'
    elif query is not None:synthetic=(query['prefix'] or '')+re.sub(r'\bcreature\b','creatures',query['subject'],flags=re.I)+' have vigilance.'
    elif quoted is not None:synthetic=quoted['subject']+' have vigilance.'
    else:return None
    compiled=fixed_public_state_characteristics_handler(synthetic,source_name=source_name)
    conditional=compiled is not None
    if compiled is None:compiled=attached_fixed_characteristics_handler(synthetic,source_name=source_name)
    if compiled is None:compiled=fixed_query_keyword_grant_handler(synthetic)
    if compiled is None:return None
    _,raw,required=compiled;descriptor=dict(raw);modifier=dict(descriptor['modifier'])
    if modifier.get('add_abilities')!=['Vigilance']:return None
    modifier['add_abilities']=[];modifier['add_ability_fragments']=[fragment];descriptor['modifier']=modifier
    dependencies=set(required)-{'combat.attack.vigilance','continuous.ability.fixed_query_keyword_grant'}
    dependencies.add(AS_UNBLOCKED_CAPABILITY)
    if conditional:descriptor.update(handler_id=CONDITIONAL_AS_UNBLOCKED_HANDLER,schema_version=4)
    elif descriptor['handler_id']=='continuous.ability.fixed-query-keyword-grant.v1':
        descriptor['handler_id']='continuous.ability.fixed-query-grant.v1';dependencies.add('continuous.ability.fixed_query_grant')
    return 'static-as-unblocked-assignment-grant-v1',descriptor,tuple(sorted(dependencies))


def temporary_as_unblocked_template(text: str, *, source_name: str, source_is_permanent, source_card_types):
    match=re.fullmatch(r"You may have (?P<subject>.+?) assign (?:its|their) combat damage this turn as though (?:it|they) weren't blocked\.?",text.strip(),re.I)
    quoted=re.fullmatch(r'Until end of turn, (?P<subject>.+?) gain "You may have this creature assign its combat damage as though it weren\'t blocked\."\.?',text.strip(),re.I)
    if match is None and quoted is None:return None
    subject=(match or quoted)['subject']
    from .fixed_resolution_characteristics import _subject
    selected=_subject(subject,source_name=source_name,source_is_permanent=source_is_permanent,source_card_types=tuple(source_card_types))
    if selected is None:return None
    selection,schema=selected
    if schema is not None:return None
    effect={'op':'apply_source_characteristics_until_end_of_turn','schema_version':4,'permission':'assign_as_unblocked','mode':'grant_ability' if quoted is not None else 'assignment_rule',**selection}
    if match is not None:
        if selection.get('predicate') is not None and subject.casefold()!='creatures you control':return None
        effect={'op':'offer_optional_effect','player':'$controller','effects':[effect]}
        return ('optional-temporary-as-unblocked-rule-v1',(effect,),None,(TEMPORARY_AS_UNBLOCKED_MECHANIC,'cr-611-continuous-effects','fixed-optional-effect-choice'))
    return ('temporary-as-unblocked-assignment-v1',(effect,),None,(TEMPORARY_AS_UNBLOCKED_MECHANIC,'cr-611-continuous-effects'))
