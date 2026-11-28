"""Require the complete ordinary activation and joined meld result."""

from collections.abc import Mapping

from ..meld_model import MELD_CAPABILITY, MELD_PAIR_FIELD, MeldPairSpec, meld_pair_effects, uses_meld_pair
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


DEPENDENCIES = (
    MELD_CAPABILITY, "zone.linked_exile_return.fixed", "zone.composition.departure_order",
    "zone.composition.destination_result", "continuous.characteristics.composition_copiable_values",
)


def meld_pair_activation_node_capabilities(effects, target_schema, cost_schema):
    if (target_schema is not None or len(effects) != 2 or not isinstance(cost_schema, Mapping)
            or cost_schema.get("tap_source") is not True):
        return ()
    try:
        pair = MeldPairSpec.from_dict(effects[0][MELD_PAIR_FIELD])
    except (TypeError, ValueError, KeyError):
        return ()
    if tuple(map(FrozenMap, effects)) != tuple(map(FrozenMap, meld_pair_effects(pair))):
        return ()
    return DEPENDENCIES


def meld_pair_runtime_dependencies(program):
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    if not any(map(uses_meld_pair, nested_effects((program.effects, program.handlers)))):
        return ()
    required = meld_pair_activation_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or len(program.handlers) != 1):
        raise ValueError("Meld needs the original complete untargeted tap activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if (ability.mana_ability or not ability.compiled_cost or not ability.tap_source
            or ability.builtin_semantic_key != program.key or ability.target_schema is not None):
        raise ValueError("Meld cannot substitute another activation or payment")
    return required
