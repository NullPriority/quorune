"""Closed characteristic results on the sealed attacking object."""

from collections.abc import Mapping

from ..qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY
from ..query_effect_amount_model import (
    PublicQueryAmountSpec, PUBLIC_QUERY_AMOUNT_CAPABILITY, ATTACK_SHARED_TYPE_AMOUNT_VERSION,
    public_query_amount_runtime_dependencies,
)
from ..characteristic_fragments import CharacteristicQuantityScope,CharacteristicQuantitySpec
from .node_capability_shapes import fixed_source_characteristics_node_capabilities
from .fixed_stat_multipliers import fixed_stat_multiplier_node_capabilities, is_fixed_stat_multiplier


def is_exact_attack_shared_type_power(effect, field):
    return (
        field == "power"
        and effect.get("op") == "apply_source_characteristics_until_end_of_turn"
        and effect.get("card") == "$event.permanent_object"
        and set(effect) == {"op", "card", "power", "toughness", "keywords", "set_card_types",
                            "set_subtypes", "set_colors", "base_power", "base_toughness"}
        and type(effect.get("toughness")) is int and effect["toughness"] == 0
        and effect.get("keywords") in ([], ())
        and all(effect.get(key) is None for key in (
            "set_card_types", "set_subtypes", "set_colors", "base_power", "base_toughness",
        ))
    )


def attack_event_characteristic_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects) == 2:
        return attached_block_growth_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if effect.get('op')!='apply_source_characteristics_until_end_of_turn' or effect.get('card')!='$event.permanent_object':return ()
    if is_fixed_stat_multiplier(effect):
        return fixed_stat_multiplier_node_capabilities(effects=effects, target_schema=None, mechanic_ids=mechanic_ids)
    projected={**effect,'card':'$source.zone_object'};amounts=set()
    for field in ('power','toughness'):
        value=projected.get(field)
        if isinstance(value,Mapping):
            try:spec=PublicQueryAmountSpec.from_dict(value)
            except (ValueError,TypeError):return ()
            if spec.schema_version == ATTACK_SHARED_TYPE_AMOUNT_VERSION:
                if not is_exact_attack_shared_type_power(effect, field):
                    return ()
                projected[field] = 1
                amounts.update(public_query_amount_runtime_dependencies(value))
                continue
            if (not isinstance(spec.quantity,CharacteristicQuantitySpec)
                    or spec.quantity.scope not in {CharacteristicQuantityScope.CONTROLLER_ZONE,
                    CharacteristicQuantityScope.OPPONENT_ZONES,CharacteristicQuantityScope.ALL_ZONES}
                    or spec.quantity.exclude_source and spec.schema_version != 3
                    or spec.quantity.exclude_attached_object):return ()
            projected[field]=spec.coefficient*2;amounts.add(PUBLIC_QUERY_AMOUNT_CAPABILITY)
    base=fixed_source_characteristics_node_capabilities(effects=(projected,),target_schema=None,mechanic_ids=mechanic_ids)
    return tuple(sorted({ATTACK_CHARACTERISTIC_RESULT_CAPABILITY,'resolution.event_object.fixed_result',*base,*amounts})) if base else ()


ATTACHED_BLOCK_GROWTH_CAPABILITY = "resolution.event_object.attached_block_growth_and_life"
ATTACHED_BLOCK_GROWTH_VARIANT = "enchanted_creature_blocks_or_becomes_blocked"
ATTACHED_BLOCK_GROWTH_TEMPLATE = "attached-block-occurrence-growth-and-life-v1"


def attached_block_growth_condition():
    from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition

    attached = {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.card"}
    return fixed_event_subscription_condition(tuple(
        FixedEventSubscription(event, attached) for event in ("creature.blocks", "creature.becomes_blocked")
    ))


def attached_block_growth_effects(power, toughness, life):
    if any(type(number) is not int for number in (power, toughness, life)) or not (power or toughness) or life < 1:
        raise ValueError("An attached block result requires fixed stats and positive life")
    return (
        {"op": "apply_source_characteristics_until_end_of_turn", "card": "$event.permanent_object",
         "power": power, "toughness": toughness, "keywords": [], "set_card_types": None,
         "set_subtypes": None, "set_colors": None, "base_power": None, "base_toughness": None},
        {"op": "life", "player": "$controller", "delta": life},
    )


def attached_block_growth_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..replacement.immutable import thaw_value

    if target_schema is not None or len(effects) != 2 or not isinstance(effects[0], Mapping) or not isinstance(effects[1], Mapping):
        return ()
    stat, life = effects
    if stat.get("card") != "$event.permanent_object" or stat.get("op") != "apply_source_characteristics_until_end_of_turn":
        return ()
    try:
        expected = attached_block_growth_effects(stat.get("power"), stat.get("toughness"), life.get("delta"))
    except ValueError:
        return ()
    if tuple(thaw_value(effect) for effect in effects) != expected:
        return ()
    return (ATTACHED_BLOCK_GROWTH_CAPABILITY, "resolution.event_object.fixed_result",
            "continuous.resolution.fixed_characteristics_until_end_of_turn", "life.change.effect",
            "trigger.subscription.fixed_multi_event", "attachment.reference.current_or_lki")


def attached_block_growth_runtime_dependencies(program):
    from ..replacement.immutable import thaw_value

    marked = (ATTACHED_BLOCK_GROWTH_CAPABILITY in program.capability_dependencies
              or ATTACHED_BLOCK_GROWTH_TEMPLATE in str(program.provenance.get("template_id")))
    if not marked and (program.event != "creature.blocks" or len(program.effects) != 2):
        return ()
    condition = thaw_value(program.event_condition)
    expected_condition = attached_block_growth_condition()
    if not marked and condition != expected_condition:
        return ()
    required = attached_block_growth_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or condition != expected_condition or program.handlers or program.cost_schema is not None
            or (program.event, program.active_zone) != ("creature.blocks", "battlefield")):
        raise ValueError("Attached block life and growth require both exact sealed attachment occurrences")
    return required
