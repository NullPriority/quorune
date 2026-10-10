from __future__ import annotations

"""Read-only Power-up prices over the current source and canonical entry fact."""

import hashlib
from .power_up_model import PowerUpSpec, power_up_reduction_options, reduce_power_up_requirements
from .activation_mana_cost import ActivationManaCostOption
from .util import stable_json


def current_power_up_mana_options(host, source, ability, options):
    if not isinstance(ability.power_up,PowerUpSpec) or source is None or source.zone!='battlefield':
        raise ValueError('Power-up pricing requires its current battlefield source')
    turn=host.state.turn_sequence
    if type(turn) is not int or turn<0:raise ValueError('Power-up turn identity is malformed')
    entry=source.entered_battlefield_turn_sequence
    if type(entry) is not int or entry<0:raise ValueError('Power-up entry identity is malformed')
    if turn==0 or entry!=turn:
        return tuple(options)
    cost=host._effective_card_data(source).get('mana_cost')
    reductions=power_up_reduction_options(cost)
    variants={}
    for option in options:
        for reduction in reductions:
            requirements=reduce_power_up_requirements(option.requirements,reduction)
            identity={'requirements':dict(requirements),'life':option.life_payment,'snow':option.snow_payment}
            digest=hashlib.sha256(stable_json(identity).encode('utf-8')).hexdigest()[:16]
            variants[digest]=ActivationManaCostOption('power-up-'+digest,requirements,
                life_payment=option.life_payment,snow_payment=option.snow_payment)
            if len(variants)>128:raise ValueError('Power-up payable alternatives exceed the closed bound')
    return tuple(variants[key] for key in sorted(variants))
