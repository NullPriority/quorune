from __future__ import annotations

"""Bind an Entwine price to all independently closed modal branches."""

from collections.abc import Mapping
from ..entwine_model import ENTWINE_FIXED_CAPABILITY,ENTWINE_FIXED_MECHANIC,EntwineModeCost


def entwine_modal_cost_capabilities(*,effects,target_schema,mechanics,cost_schema,modal_resolver):
    if (ENTWINE_FIXED_MECHANIC not in mechanics or not isinstance(cost_schema,Mapping)
            or set(cost_schema)!={'entwine'}):return ()
    try:
        price=EntwineModeCost.from_dict(cost_schema['entwine']);price.validate_schema(target_schema)
    except (TypeError,ValueError,KeyError):return ()
    ordinary=tuple(value for value in mechanics if value!=ENTWINE_FIXED_MECHANIC)
    dependencies=modal_resolver(effects=effects,target_schema=target_schema,mechanics=ordinary)
    return tuple(sorted({*dependencies,ENTWINE_FIXED_CAPABILITY})) if dependencies else ()
