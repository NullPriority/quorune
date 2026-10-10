from __future__ import annotations

"""Shared pure emission of represented fixed characteristic layers."""

from ..continuous_effect_model import ContinuousEffect, ContinuousOperation, Layer


def fixed_characteristic_effects(node, context, *, common):
    """Emit the existing layer plan for one validated modifier and subject."""
    effects: list[ContinuousEffect] = []
    if node.type_operations:
        effects.append(
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}:4",
                layer=Layer.TYPE,
                sublayer="4",
                operations=node.type_operations,
                **common,
            )
        )
    if node.color_operations:
        effects.append(
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}:5",
                layer=Layer.COLOR,
                sublayer="5",
                operations=node.color_operations,
                **common,
            )
        )
    ability_operations = (
        *((ContinuousOperation("remove_all_abilities"),) if node.remove_all_abilities else ()),
        *(
            ContinuousOperation("remove_ability", ability)
            for ability in node.remove_abilities
        ),
        *(
            ContinuousOperation("add_ability", ability)
            for ability in node.add_abilities
        ),
        *(
            ContinuousOperation("add_rules_text", line)
            for line in node.add_rules_text
        ),
        *(
            ContinuousOperation("add_ability_fragment", fragment)
            for fragment in node.add_ability_fragments
        ),
    )
    if ability_operations:
        effects.append(
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}:6",
                layer=Layer.ABILITY,
                sublayer="6",
                operations=ability_operations,
                **common,
            )
        )
    if node.base_power is not None:
        effects.append(
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}:7b",
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7b",
                operations=(
                    ContinuousOperation(
                        "set_power_toughness",
                        [node.base_power, node.base_toughness],
                    ),
                ),
                **common,
            )
        )
    dynamic_power = 0
    dynamic_toughness = 0
    if node.quantity is not None and context.resolved_quantity is not None:
        dynamic_power = node.quantity_power * context.resolved_quantity
        dynamic_toughness = (
            node.quantity_toughness * context.resolved_quantity
        )
    if (
        node.power
        or node.toughness
        or dynamic_power
        or dynamic_toughness
    ):
        effects.append(
            ContinuousEffect(
                effect_id=f"{context.source_object_id}:{context.component_id}:7c",
                layer=Layer.POWER_TOUGHNESS,
                sublayer="7c",
                operations=(
                    ContinuousOperation(
                        "modify_power_toughness",
                        [
                            node.power + dynamic_power,
                            node.toughness + dynamic_toughness,
                        ],
                    ),
                ),
                **common,
            )
        )
    return tuple(effects)
