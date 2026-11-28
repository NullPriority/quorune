"""A complete optional mana payment on its original declared attack event."""

from ..compiler.optional_payment_templates import FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY
from ..extra_combat_model import (
    EXTRA_COMBAT_CAPABILITY, PAID_ATTACKING_COMBAT_CAPABILITY, uses_paid_attacking_combat,
)
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


ATTACK_TRIGGER_DEPENDENCIES = (
    PAID_ATTACKING_COMBAT_CAPABILITY, EXTRA_COMBAT_CAPABILITY, "permanent.tap_state.fixed_set",
    "state_query.permanent.public_state_predicate", FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
)
_ATTACK_CONDITION = FrozenMap({"field": "card", "op": "eq", "value": "$source.ref"})


def paid_attacking_extra_combat_runtime_dependencies(program):
    from .fixed_effect_clause_shapes import fixed_optional_mana_payment_node_capabilities

    if not any(map(uses_paid_attacking_combat, nested_effects((program.effects, program.handlers)))):
        return ()
    required = fixed_optional_mana_payment_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not set(ATTACK_TRIGGER_DEPENDENCIES).issubset(required)
            or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or program.event_condition != _ATTACK_CONDITION or program.handlers or program.cost_schema is not None):
        raise ValueError("Paid attacker untap and extra combat require the original untargeted self-attack and optional payment")
    return ATTACK_TRIGGER_DEPENDENCIES
