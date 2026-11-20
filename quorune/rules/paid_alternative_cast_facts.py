"""Independent ownership for sealed paid-alternative result instructions."""
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from ..paid_alternative_cast_model import (
    PAID_ALTERNATIVE_CAST_CAPABILITY as CAP,PAID_ALTERNATIVE_CAST_MECHANIC as MECHANIC,
    PAID_ALTERNATIVE_CAST_FIELD as FIELD,PAID_ALTERNATIVE_RESULT_VERSION as VERSION,
    PAID_ALTERNATIVE_ENTRY_CONDITION,PAID_ALTERNATIVE_ENTRY_FIELD,
    PaidAlternativeCastRequirement,PaidAlternativeCastFact,PaidAlternativeEntryInformation,
    validate_paid_alternative_instruction,validated_paid_entry_occurrence,
)

_OP='apply_if_public_condition'
_REFERENCE='$context.'+FIELD
_RESOLVING_ENTRY=ContextVar('paid_alternative_resolving_entry',default=None)


def _result_dependencies(effects,mechanics):
    from .capabilities import capability_dependencies_for_node
    required=capability_dependencies_for_node(effects=effects,target_schema=None,mechanic_ids=mechanics)
    if not required:raise ValueError('Paid alternative result lacks its concrete existing result owner')
    return set(required)


def paid_alternative_payload_dependencies(effect):
    if effect.get('op')!=_OP or effect.get('schema_version')!=VERSION:return ()
    _requirement,children=validate_paid_alternative_instruction(effect)
    from ..resolution_conditions import RESOLUTION_CONDITION_CAPABILITY
    return tuple(sorted({CAP,RESOLUTION_CONDITION_CAPABILITY,*_result_dependencies(children,effect['mechanic_ids'])}))


def paid_alternative_result_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if (not effects or effects[-1].get('op')!=_OP or effects[-1].get('schema_version')!=VERSION
            or target_schema is not None or MECHANIC not in mechanic_ids):return ()
    try:
        wrapper=effects[-1];required=set(paid_alternative_payload_dependencies(wrapper))
        if wrapper['player']!='$controller' or len(effects)>8:return ()
        if effects[:-1]:required.update(_result_dependencies(tuple(effects[:-1]),wrapper['prefix_mechanic_ids']))
        elif wrapper['prefix_mechanic_ids']:return ()
        return tuple(sorted(required))
    except (ValueError,TypeError,KeyError):return ()


def paid_alternative_program_dependencies(program):
    has_result=any(isinstance(e,Mapping) and e.get('op')==_OP and e.get('schema_version')==VERSION for e in program.effects)
    entry=(getattr(program,'event_condition',None)=={'field':PAID_ALTERNATIVE_ENTRY_CONDITION,'op':'eq','value':'surge'})
    if not has_result and not entry:return ()
    from ..card_programs.admission import program_has_declared_complete_source_admission
    if not program_has_declared_complete_source_admission(program):
        raise ValueError('Paid alternative facts require declared complete source admission')
    if entry:
        if program.event!='permanent.enter.self' or program.active_zone!='battlefield':
            raise ValueError('Paid entry fact requires its original self-entry occurrence')
        return (CAP,)
    if program.event!='resolve' or program.active_zone!='stack' or not str(program.ability_id).startswith('spell:'):
        raise ValueError('Paid alternative result requires its resolving spell frame')
    required=paid_alternative_result_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=(MECHANIC,))
    if not required:raise ValueError('Paid spell result and mandatory prefix are not independently closed')
    return required


def paid_alternative_fact_from_option(requirement,selected_option):
    """Only a server-selected typed price supplies the payment decision."""
    if not isinstance(requirement,PaidAlternativeCastRequirement) or not isinstance(selected_option,Mapping):
        raise ValueError('Paid alternative casting needs its typed requirement and authorized option')
    raw=selected_option.get('fixed_public_alternative_cost');paid=False
    if raw is not None:
        from ..public_alternative_costs import FixedPublicAlternativeCostSpec,FixedPublicAlternativeCostKind
        spec=FixedPublicAlternativeCostSpec.from_dict(raw)
        paid=spec.kind is FixedPublicAlternativeCostKind.SURGE
    return PaidAlternativeCastFact(requirement.method,paid)


def _source_requirements(host,card,*,entry_only=False):
    from ..card_program_faces import program_matches_face
    programs=getattr(getattr(host,'semantics',None),'programs_for_oracle',None)
    reader=getattr(host,'card_record',None)
    if not callable(programs) or not callable(reader):return ()
    record=reader(card)
    if record is None:return ()
    requirements=set()
    for program in programs(record.oracle_id):
        if (CAP not in program.capability_dependencies or not host.semantic_program_is_current_trusted(program)
                or not program_matches_face(record,program,card)
                or entry_only and program.event!='permanent.enter.self'):continue
        if paid_alternative_program_dependencies(program):requirements.add(PaidAlternativeCastRequirement('surge'))
    if len(requirements)>1:raise ValueError('Paid alternative source requirements disagree')
    return tuple(requirements)


def paid_alternative_cast_context(host,card,selected_option):
    requirements=_source_requirements(host,card)
    if not requirements:return {}
    return {FIELD:paid_alternative_fact_from_option(requirements[0],selected_option).to_dict()}


@contextmanager
def resolving_paid_alternative_entry(host,item):
    card=host.state.cards.get(item.card_object_id or '')
    if card is None or not _source_requirements(host,card,entry_only=True):
        yield;return
    if card.zone!='stack' or item.kind not in {'spell','spell_copy'}:
        raise ValueError('Paid entry scope requires its actual resolving spell or spell copy')
    fact=PaidAlternativeCastFact.from_dict(item.context.get(FIELD))
    token=_RESOLVING_ENTRY.set((host,card.object_id,card.logical_object_id,fact))
    try:yield
    finally:_RESOLVING_ENTRY.reset(token)


def information_for_paid_entry(host,card,departure):
    if card.zone!='battlefield':return None
    requirements=_source_requirements(host,card,entry_only=True)
    if not requirements:return None
    current=_RESOLVING_ENTRY.get();fact=PaidAlternativeCastFact(requirements[0].method,False);stack=None
    if current is not None and current[0] is host and current[1]==card.object_id and departure.origin=='stack':
        if current[2]!=departure.logical_object_id:raise ValueError('Paid entry changed the resolving stack incarnation')
        stack,fact=current[2],current[3]
    from ..continuous_effect_model import ContinuousObjectIdentity
    return PaidAlternativeEntryInformation(ContinuousObjectIdentity(card.object_id,card.logical_object_id),fact,stack).to_dict()
