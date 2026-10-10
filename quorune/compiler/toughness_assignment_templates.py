from __future__ import annotations

"""Closed source-local assignment rules with existing characteristic prefixes."""

import re
from ..ability_fragments import ability_fragment_to_dict
from ..toughness_assignment_model import ToughnessAssignmentSpec,TOUGHNESS_ASSIGNMENT_CAPABILITY,TOUGHNESS_ASSIGNMENT_HANDLER,TEMPORARY_TOUGHNESS_MECHANIC
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec
from .continuous_templates import attached_fixed_characteristics_handler
from .defender_permission_templates import static_defender_permission_handler

_RESULT=r'assigns? combat damage equal to (?:its|their) toughness rather than (?:its|their) power'


def static_toughness_assignment_handler(text: str, *, source_name: str):
    normalized=text.strip().rstrip('.')
    during=False
    if normalized.casefold().startswith('during your turn, '):during=True;normalized=normalized[len('During your turn, '):]
    prefix=None;scope=None;greater=False;keywords=()
    attached=re.fullmatch(rf'(?P<subject>Enchanted creature|Equipped creature)(?: gets (?P<stats>[+-]\d+/[+-]\d+) and)? {_RESULT}',normalized,re.I)
    if attached is not None:
        scope='attached'
        if attached['stats']:prefix=attached_fixed_characteristics_handler(attached['subject']+' gets '+attached['stats']+'.',source_name=source_name)
    conditional=re.fullmatch(rf"As long as (?P<subject>enchanted|equipped) creature(?:'s toughness is greater than its power| has (?P<keyword>vigilance)), it {_RESULT}",normalized,re.I)
    if conditional is not None:
        scope='attached';greater=conditional['keyword'] is None;keywords=(conditional['keyword'].casefold(),) if conditional['keyword'] else ()
    source=rf'(?:This creature|{SourceReferenceSpec(source_name).regex_pattern})'
    if re.fullmatch(rf'{source} {_RESULT}',normalized,re.I):scope='self'
    queried=re.fullmatch(rf"(?P<subject>Each creature|Creatures)(?P<controller> you control)?(?P<quality> with toughness greater than (?:its|their) power| with defender)? {_RESULT}(?P<defender> and can attack as though it didn't have defender)?",normalized,re.I)
    if queried is not None:
        scope='controller' if queried['controller'] else 'all';greater=bool(queried['quality'] and 'greater' in queried['quality'])
        keywords=('defender',) if queried['quality'] and 'defender' in queried['quality'] else ()
        if queried['defender']:
            if greater:return None
            subject=('Creatures you control' if scope=='controller' else 'Creatures')+(' with defender' if keywords else '')
            prefix=static_defender_permission_handler(subject+" can attack as though they didn't have defender.",source_name=source_name)
            if prefix is None:return None
    if scope is None:return None
    rule=ToughnessAssignmentSpec(scope=scope,predicate=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),keywords_all=keywords),
        toughness_greater_than_power=greater,during_controller_turn=during)
    descriptor={'handler_id':TOUGHNESS_ASSIGNMENT_HANDLER,'schema_version':1,'event':'characteristics.evaluate','fragment':ability_fragment_to_dict(rule)}
    if prefix is None:return 'static-toughness-assignment-v1',descriptor,(TOUGHNESS_ASSIGNMENT_CAPABILITY,)
    return 'static-toughness-assignment-composition-v1', (prefix[1],descriptor),tuple(sorted({TOUGHNESS_ASSIGNMENT_CAPABILITY,*prefix[2]}))


def temporary_toughness_assignment_template(text: str):
    normalized=text.strip().rstrip('.')
    coupled=re.fullmatch(r"Until end of turn, target creature with defender gains haste, can attack as though it didn't have defender, and assigns combat damage equal to its toughness rather than its power",normalized,re.I)
    if coupled is not None:
        from .closed_effect_programs import ClosedEffectProgramTemplate
        from .direct_target import direct_permanent_target_spec
        schema=direct_permanent_target_spec('target creature with defender').to_target_schema()
        effects=(
            {'op':'grant_keyword_until_end_of_turn','card':'$target.0','keyword':'Haste'},
            {'op':'apply_source_characteristics_until_end_of_turn','schema_version':3,'permission':'ignore_defender',
             'power':0,'toughness':0,'keywords':[],'card':'$target.0'},
            {'op':'apply_source_characteristics_until_end_of_turn','schema_version':5,'permission':'use_toughness','card':'$target.0'},
        )
        return ClosedEffectProgramTemplate(('grant-haste-v1','temporary-defender-v1','temporary-toughness-v1'),(1,1,1),effects,schema,
            ('closed-effect-program','fixed-temporary-defender-permission',TEMPORARY_TOUGHNESS_MECHANIC,'cr-611-continuous-effects','cr-115-targets','haste')).compiled()
    prefix=re.fullmatch(rf'Until end of turn, (?P<subject>target creature(?: you control)?) {_RESULT}',normalized,re.I)
    suffix=re.fullmatch(rf'(?P<subject>target creature(?: you control)?) {_RESULT} this turn',normalized,re.I)
    match=prefix or suffix
    if match is None:return None
    from .direct_target import direct_permanent_target_spec
    target=direct_permanent_target_spec(match['subject'])
    if target is None:return None
    effect={'op':'apply_source_characteristics_until_end_of_turn','schema_version':5,'permission':'use_toughness','card':'$target.0'}
    return 'temporary-target-toughness-assignment-v1',(effect,),target.to_target_schema(),(TEMPORARY_TOUGHNESS_MECHANIC,'cr-115-targets')
