"""Complete upkeep-condition and current recipient agreement, without labels."""
from ..player_maximum_model import PlayerMaximumSpec,PLAYER_MAXIMUM_CAPABILITY,PLAYER_MAXIMUM_FACT
from ..continuous_conditions import FixedPublicStateConditionSpec
from .event_subscriptions import _public_state_leaves


def player_maximum_control_dependencies(program,reference):
    metric=PlayerMaximumSpec.from_reference(reference)
    leaves=tuple(_public_state_leaves(program.event_condition))
    if len(leaves)!=1:raise ValueError('Unique maximum control lost its intervening condition')
    leaf=leaves[0]
    if set(leaf)!={'field','op','value','condition'} or leaf['op']!='truthy' or leaf['value'] is not True:
        raise ValueError('Unique maximum control has a malformed intervening condition')
    condition=FixedPublicStateConditionSpec.from_dict(leaf['condition'])
    schedule={'all':[{'field':'player','op':'eq','value':'$source.controller'},
        {'field':'step','op':'eq','value':'upkeep'}]}
    if (condition.fact is None or condition.fact.value!=PLAYER_MAXIMUM_FACT or condition.fact_parameter!=metric.metric
            or program.event!='step.begin' or program.active_zone!='battlefield' or program.target_schema is not None
            or program.event_condition!={'all':[schedule,leaf]}):
        raise ValueError('Unique maximum control lost its matching upkeep and metric')
    return (PLAYER_MAXIMUM_CAPABILITY,'trigger.condition.fixed_public_state')
