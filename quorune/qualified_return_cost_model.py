"""Versioned public-subtype return prices; canonical return keeps mutation."""
from collections.abc import Mapping
from .additional_cost_vocabulary import RETURN_ONE_TO_OWNER_HAND_COST, ZONE_CHANGE_COST_KIND
from .basic_land_types import BASIC_LAND_MANA
from .creature_subtypes import canonical_creature_subtype
from .object_predicate import ObjectQuerySpec

QUALIFIED_RETURN_COST_VERSION = 7
QUALIFIED_RETURN_CHOICE_FIELD = 'return_schema_version'
SELECTED_RETURN_CAPABILITY = 'activation.selected_zone_change.fixed'


def validate_qualified_return_cost(cost):
    query = cost.predicate
    subtype = query.subtypes_all[0] if len(query.subtypes_all) == 1 else None
    if (cost.operation != RETURN_ONE_TO_OWNER_HAND_COST or cost.unblocked_attacker
            or subtype is None or subtype not in BASIC_LAND_MANA and canonical_creature_subtype(subtype) is None
            or query != ObjectQuerySpec(zones=('battlefield',), controller='$actor',
                known_to_actor=True, subtypes_all=(subtype,))):
        raise ValueError('Qualified return requires one public controlled subtype')


def qualified_return_cost_payload_dependencies(value):
    """Decode only explicit new versions, leaving legacy price data unchanged."""
    from .rules.casting_additional_costs import FixedZoneChangeAdditionalCost
    if value.get('kind') == ZONE_CHANGE_COST_KIND and value.get('schema_version') == QUALIFIED_RETURN_COST_VERSION:
        cost = FixedZoneChangeAdditionalCost.from_descriptor(value)
        from .rules.casting_additional_cost_groups import fixed_zone_change_additional_cost_capability
        return (fixed_zone_change_additional_cost_capability(cost),)
    if QUALIFIED_RETURN_CHOICE_FIELD not in value:
        return ()
    if type(value[QUALIFIED_RETURN_CHOICE_FIELD]) is not int or value[QUALIFIED_RETURN_CHOICE_FIELD] != QUALIFIED_RETURN_COST_VERSION:
        raise ValueError('Unsupported qualified-return choice version')
    from .activation_cost_choice import CostChoice
    if 'k' in value:
        if set(value) != {'k', 'n', 'z', 'q', QUALIFIED_RETURN_CHOICE_FIELD} or not isinstance(value['q'], Mapping):
            raise ValueError('Qualified-return compact price fields are closed')
        choice = CostChoice(kind=value['k'], count=value['n'], zone=value['z'],
            predicate=value['q'], return_schema_version=value[QUALIFIED_RETURN_CHOICE_FIELD])
    else:
        choice = CostChoice.from_dict(value)
    if choice.fixed_zone_change_cost().schema_version != QUALIFIED_RETURN_COST_VERSION:
        raise ValueError('Qualified return lost its typed price owner')
    return (SELECTED_RETURN_CAPABILITY,)
