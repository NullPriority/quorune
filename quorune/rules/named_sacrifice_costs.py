from __future__ import annotations

"""Independent capability checks for full and compact named sacrifice prices."""

from collections.abc import Mapping

from .named_sacrifice_cost_model import (
    NAMED_SACRIFICE_CAPABILITY,
    NAMED_SACRIFICE_COST_VERSION,
    NAMED_SACRIFICE_NAME_FIELD,
)


def named_sacrifice_payload_dependencies(value):
    version = NAMED_SACRIFICE_COST_VERSION
    if value.get("kind") == "zone_change" and value.get("schema_version") == version:
        from .casting_additional_costs import FixedZoneChangeAdditionalCost

        FixedZoneChangeAdditionalCost.from_descriptor(value)
        return (NAMED_SACRIFICE_CAPABILITY,)
    if NAMED_SACRIFICE_NAME_FIELD not in value and value.get("sacrifice_schema_version") != version:
        return ()
    from ..activation_cost_choice import CostChoice

    if "k" in value:
        fields = {"k", "n", "z", "q", "sacrifice_schema_version", NAMED_SACRIFICE_NAME_FIELD}
        if set(value) != fields or not isinstance(value["q"], Mapping):
            raise ValueError("Named sacrifice compact price fields are closed")
        choice = CostChoice(
            kind=value["k"], count=value["n"], zone=value["z"], predicate=value["q"],
            sacrifice_schema_version=value["sacrifice_schema_version"],
            sacrifice_name=value[NAMED_SACRIFICE_NAME_FIELD],
        )
    else:
        choice = CostChoice.from_dict(value)
    cost = choice.fixed_zone_change_cost()
    if cost is None or cost.schema_version != version:
        raise ValueError("Named sacrifice lost its exact selected zone-cost owner")
    return ("activation.selected_zone_change.fixed", NAMED_SACRIFICE_CAPABILITY)


def qualified_sacrifice_payload_dependencies(value):
    from .keyword_sacrifice_costs import keyword_sacrifice_payload_dependencies

    return named_sacrifice_payload_dependencies(value) or keyword_sacrifice_payload_dependencies(value)
