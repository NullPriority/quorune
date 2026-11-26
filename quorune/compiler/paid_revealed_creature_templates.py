"""Lower the fixed creature reveal price and its supported printed results."""

from ..rules.paid_revealed_creature_results import (
    PAID_REVEALED_CREATURE_MECHANIC, creature_reveal_cost_schema,
    paid_revealed_creature_node_capabilities, paid_revealed_creature_result,
)
from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC
from .dependency_gate import DependencyGate, explicit_capabilities_gate
from .spell_additional_cost_templates import FixedRevealAdditionalCostTemplate


def mandatory_creature_reveal_template(text):
    if text.strip().casefold() != "as an additional cost to cast this spell, reveal a creature card from your hand.":
        return None
    return FixedRevealAdditionalCostTemplate(None)


def paid_revealed_creature_template(text, *, cost_schema, source_name):
    if cost_schema != creature_reveal_cost_schema():
        return None
    source = source_name.casefold()
    bodies = {
        "target creature gets -x/-x until end of turn, where x is the revealed card's mana value.": "stat-reduction",
        f"{source} deals x damage to each creature your opponents control, where x is the revealed card's mana value.": (
            "opposing-creature-damage"
        ),
        f"{source} deals damage to target creature or planeswalker equal to the revealed card's power.": "target-damage",
        "you gain life equal to the revealed card's mana value.": "life-gain",
    }
    variant = bodies.get(text.strip().replace("’", "'").casefold())
    if variant is None:
        return None
    effect, schema, _ = paid_revealed_creature_result(variant)
    return (
        f"paid-reveal-{variant}-v1", (effect,), schema,
        (PAID_REVEALED_CREATURE_MECHANIC, SCALAR_AMOUNT_MECHANIC),
    )


def paid_revealed_creature_gate(*, effects, target_schema, mechanics, cost_schema,
                              capability_registry, capability_profile):
    if PAID_REVEALED_CREATURE_MECHANIC not in mechanics:
        return None
    required = paid_revealed_creature_node_capabilities(
        effects=effects, target_schema=target_schema, mechanics=mechanics, cost_schema=cost_schema,
    )
    if not required:
        return DependencyGate(blockers=("Paid creature reveal requires the complete fixed spell",))
    return explicit_capabilities_gate(
        required, capability_registry=capability_registry, capability_profile=capability_profile,
    )
