from __future__ import annotations

"""Closed public game-history quantity for command-zone commander casts."""

COMMANDER_CAST_COUNT_CAPABILITY = "quantity_expression.commander_cast_count"


def validate_commander_cast_quantity(quantity):
    from .characteristic_fragments import CharacteristicQuantityReduction as Reduction
    from .characteristic_fragments import CharacteristicQuantityScope as Scope

    if (quantity.schema_version != 8 or quantity.scope is not Scope.CONTROLLER_COMMANDER_CAST_COUNT
            or quantity.query is not None or quantity.counter_name is not None
            or quantity.exclude_source or quantity.exclude_attached_object
            or quantity.reduction is not Reduction.OBJECT_COUNT
            or quantity.multiplier != 1 or quantity.offset != 0 or quantity.terms
            or quantity.mana_colors or quantity.literal_name is not None):
        raise ValueError("Commander cast counts require one unscaled public game-history quantity")
