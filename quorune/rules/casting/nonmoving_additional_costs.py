from __future__ import annotations

"""Selection for typed additional prices that do not move their cost objects."""

from ..casting_additional_costs import AdditionalCostError
from ..reveal_additional_cost import fixed_reveal_additional_cost
from ..tap_additional_cost import fixed_tap_additional_cost
from .reveal_costs import reveal_cost_selection
from .additional_tap_costs import validate_fixed_tap_payment


def select_fixed_nonmoving_cost(host,seat,source,option,mandatory,response,*,hint,choice_schema,position):
    """None delegates other kinds; a result carries legality and an optional selection."""
    try:
        tap=fixed_tap_additional_cost(mandatory[position])
        reveal=fixed_reveal_additional_cost(mandatory[position])
    except AdditionalCostError:return False,None
    if tap is None and reveal is None:return None
    if len(mandatory)!=1:return False,None
    if tap is not None:
        try:validate_fixed_tap_payment(host,seat,source,option)
        except ValueError:return False,None
        return True,None
    return reveal_cost_selection(host,seat=seat,source=source,cost=reveal,response=response,
        hint=hint,choice_schema=choice_schema,cost_position=position)
