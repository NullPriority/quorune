"""Payload-owned entry condition dependency independent of coverage labels."""
from collections.abc import Mapping
from ..tribute_model import TRIBUTE_CAPABILITY,TRIBUTE_UNPAID_FIELD
from ..replacement.immutable import thaw_value


def tribute_condition_dependencies(program):
    condition=program.event_condition
    if not isinstance(condition,Mapping) or condition.get('field')!=TRIBUTE_UNPAID_FIELD:return ()
    if (thaw_value(condition)!={'field':TRIBUTE_UNPAID_FIELD,'op':'contains_any','value':['tribute']}
            or program.event!='permanent.enter.self' or program.active_zone!='battlefield'):
        raise ValueError('Tribute condition requires its exact source-entry fact')
    return (TRIBUTE_CAPABILITY,)
