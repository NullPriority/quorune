"""Closed spell results from a single creature revealed to pay its casting cost."""

from collections.abc import Mapping

from ..affected_permanents import PermanentControllerRelation
from ..compiler.direct_target import DirectPermanentTargetSpec
from ..fixed_damage_set_model import PermanentDamageGroup
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..scalar_effect_amount_model import (
    PAID_REVEALED_CREATURE_CAPABILITY, PAID_REVEALED_CREATURE_VERSION,
    SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_KIND, ScalarAmountOrigin, ScalarEffectAmountSpec,
)
from ..util import stable_json
from .reveal_additional_cost import (
    CREATURE_TYPE_REVEAL_VERSION, REVEAL_ADDITIONAL_COST_CAPABILITY,
    REVEAL_ADDITIONAL_COST_KIND, FixedRevealAdditionalCost,
)


PAID_REVEALED_CREATURE_MECHANIC = "spell-result-from-paid-creature-reveal"
_VARIANTS = ("stat-reduction", "opposing-creature-damage", "target-damage", "life-gain")
_DAMAGE_DEPENDENCIES = (
    "damage.amount.positive", "damage.result.multitype_permanent", "damage.source.stack_current_or_lki",
)


def creature_reveal_cost_schema():
    price = FixedRevealAdditionalCost(None, schema_version=CREATURE_TYPE_REVEAL_VERSION)
    return {"additional_costs": [price.to_descriptor()]}


def _paid_amount(characteristic, *, coefficient=1):
    return ScalarEffectAmountSpec(
        ScalarAmountOrigin.PAID_REVEALED_CREATURE, characteristic=characteristic,
        coefficient=coefficient, schema_version=PAID_REVEALED_CREATURE_VERSION,
    ).to_dict()


def paid_revealed_creature_result(variant):
    if variant == "stat-reduction":
        effect = {"op": "modify_stats_until_end_of_turn", "card": "$target.0",
                  "power": _paid_amount("mana_value", coefficient=-1),
                  "toughness": _paid_amount("mana_value", coefficient=-1)}
        schema = DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema()
        requirements = ("continuous.resolution.fixed_characteristics_until_end_of_turn", "target.revalidate_resolution")
        return effect, schema, requirements
    if variant == "opposing-creature-damage":
        group = PermanentDamageGroup(
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
            controller_relation=PermanentControllerRelation.OPPONENTS,
        )
        effect = {"op": "damage_fixed_set", "source": "$source", "amount": _paid_amount("mana_value"),
                  "groups": [group.to_dict()]}
        return effect, None, (*_DAMAGE_DEPENDENCIES, "damage.batch.fixed_set")
    if variant == "target-damage":
        effect = {"op": "damage", "source": "$source", "target": "$target.0", "amount": _paid_amount("power")}
        schema = DirectPermanentTargetSpec(types_any=("creature", "planeswalker")).to_target_schema()
        return effect, schema, (*_DAMAGE_DEPENDENCIES, "target.revalidate_resolution")
    if variant == "life-gain":
        return {"op": "life", "player": "$controller", "delta": _paid_amount("mana_value")}, None, ("life.change.effect",)
    raise ValueError("Unknown creature reveal spell result")


def paid_revealed_creature_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if (PAID_REVEALED_CREATURE_MECHANIC not in mechanics or len(effects) != 1
            or stable_json(thaw_value(cost_schema)) != stable_json(creature_reveal_cost_schema())):
        return ()
    payload = stable_json(thaw_value(effects[0]))
    target = stable_json(thaw_value(target_schema))
    for variant in _VARIANTS:
        effect, schema, result = paid_revealed_creature_result(variant)
        if payload == stable_json(effect) and target == stable_json(schema):
            return (REVEAL_ADDITIONAL_COST_CAPABILITY, SCALAR_AMOUNT_CAPABILITY,
                    PAID_REVEALED_CREATURE_CAPABILITY, *result)
    return ()


def _contains_paid_reveal_shape(value):
    if isinstance(value, Mapping):
        return (
            (value.get("kind"), value.get("origin")) == (SCALAR_AMOUNT_KIND, ScalarAmountOrigin.PAID_REVEALED_CREATURE)
            or (value.get("kind"), value.get("schema_version")) == (REVEAL_ADDITIONAL_COST_KIND, CREATURE_TYPE_REVEAL_VERSION)
            or any(_contains_paid_reveal_shape(child) for child in value.values())
        )
    return isinstance(value, (tuple, list)) and any(_contains_paid_reveal_shape(child) for child in value)


def paid_revealed_creature_runtime_dependencies(program):
    if (
        PAID_REVEALED_CREATURE_MECHANIC not in program.coverage
        and PAID_REVEALED_CREATURE_CAPABILITY not in program.capability_dependencies
        and not _contains_paid_reveal_shape((program.effects, program.handlers, program.cost_schema))
    ):
        return ()
    required = paid_revealed_creature_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        cost_schema=program.cost_schema,
    )
    if (
        not required or (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:") or program.event_condition is not None or program.handlers
    ):
        raise ValueError("Paid creature reveal requires one complete spell result and its exact price")
    return required


def is_closed_paid_revealed_creature_result(program):
    try:
        required = paid_revealed_creature_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
