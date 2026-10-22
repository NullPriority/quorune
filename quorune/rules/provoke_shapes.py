from __future__ import annotations

"""The correlated targeted optional block-and-untap keyword program."""

from .blocking_requirements import PROVOKE_CAPABILITY
from ..blocking_requirement_model import BLOCKING_REQUIREMENT_CAPABILITY,BLOCKING_REQUIREMENT_OPERATION


def provoke_program_runtime_dependencies(program):
    if 'provoke' not in program.coverage:return ()
    schema={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'count':1,'controller_seat':'$context.defending_player'}
    effects=[{'op':'offer_optional_effect','player':'$controller','effects':[
        {'op':BLOCKING_REQUIREMENT_OPERATION,'attacker':'$source.zone_object','blocker':'$target.0','untap_blocker':True}]}]
    if (program.event!='creature.attacks' or program.active_zone!='battlefield'
            or program.event_condition!={'field':'card','op':'eq','value':'$source.ref'}
            or program.target_schema!=schema or program.effects!=effects):
        raise ValueError('Provoke needs its exact defending-player target and optional correlated result')
    return (PROVOKE_CAPABILITY,BLOCKING_REQUIREMENT_CAPABILITY,'effect.choice.optional_fixed','permanent.untap.effect','target.revalidate_resolution')
