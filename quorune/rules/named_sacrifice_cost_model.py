from __future__ import annotations

"""A literal public rules-name qualifier for a six-creature activation price."""

from ..additional_cost_vocabulary import SACRIFICE_ONE_COST
from ..object_predicate import ObjectQuerySpec


NAMED_SACRIFICE_COST_VERSION = 79
NAMED_SACRIFICE_CAPABILITY = "activation.selected_zone_change.named_creature_sacrifice"
NAMED_SACRIFICE_NAME_FIELD = "sacrifice_name"


def named_creature_sacrifice_predicate() -> ObjectQuerySpec:
    return ObjectQuerySpec(zones=("battlefield",), controller="$actor",
                           known_to_actor=True, types_all=("creature",))


def validate_named_sacrifice_cost(cost) -> None:
    name = cost.sacrifice_name
    if (type(name) is not str or not name or len(name) > 200 or name != " ".join(name.split())
            or name.startswith("$")):
        raise ValueError("A named sacrifice requires one literal rules name")
    if (cost.operation != SACRIFICE_ONE_COST or cost.count != 6 or cost.unblocked_attacker is not False
            or cost.predicate != named_creature_sacrifice_predicate()):
        raise ValueError("A named sacrifice requires six controlled creatures of the same fixed name")
