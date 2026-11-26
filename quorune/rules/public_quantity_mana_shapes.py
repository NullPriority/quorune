from __future__ import annotations

"""One closed mana instruction, with independent producer authority."""

from typing import Mapping
from ..public_quantity_mana_model import PUBLIC_QUANTITY_MANA_CAPABILITY
from ..query_effect_amount_model import PublicQueryAmountSpec, PUBLIC_QUERY_AMOUNT_KIND
from ..scalar_effect_amount_model import ScalarEffectAmountSpec, SCALAR_AMOUNT_KIND, ScalarAmountOrigin
from ..characteristic_fragments import characteristic_quantity_capabilities
from ..scalar_effect_amount_model import TARGET_HAND_COUNT_MECHANIC, direct_player_amount_target_is_closed
from .target_hand_count_shapes import target_hand_count_dependencies


def public_quantity_mana_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    del kwargs
    markers = set(mechanic_ids)
    if "closed-effect-program" in markers:
        named = fixed_then_named_graveyard_mana_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=markers,
        )
        if named:
            return ("resolution.effect_program.closed_components", *named)
    if len(effects) != 1 or 'public-quantity-mana' not in markers:
        return ()
    effect = effects[0]
    choice=effect.get('op')=='choose_mana' and effect.get('schema_version')==3
    if choice:
        from ..variable_resolution_mana_model import VariableResolutionManaChoiceSpec,VARIABLE_RESOLUTION_MANA_CAPABILITY
        from ..resolution_mana_model import RESOLUTION_MANA_CHOICE_CAPABILITY
        try:VariableResolutionManaChoiceSpec.from_effect(effect)
        except (ValueError,TypeError,KeyError):return ()
    elif set(effect) != {'op','player','color','amount','source'} or effect['op'] != 'mana':
        return ()
    if effect['player']!='$controller' or effect['source']!='$source':return ()
    if not choice and (type(effect['color']) is not str or effect['color'] not in 'WUBRGC' or len(effect['color']) != 1):
        return ()
    value = effect['amount']
    if not isinstance(value, Mapping):
        return ()
    targeted = ()
    target_markers = {"cr-115-targets", TARGET_HAND_COUNT_MECHANIC}
    try:
        if value.get('kind') == PUBLIC_QUERY_AMOUNT_KIND:
            spec = PublicQueryAmountSpec.from_dict(value)
            marker = 'public-query-effect-amount'
            if spec.coefficient != 1 or spec.schema_version != 1:
                return ()
        elif value.get('kind') == SCALAR_AMOUNT_KIND:
            spec = ScalarEffectAmountSpec.from_dict(value)
            marker = 'scalar-effect-amount'
            if spec.coefficient != 1 or spec.binding_id is not None:
                return ()
            if spec.origin is ScalarAmountOrigin.TARGET_HAND_COUNT:
                if (
                    choice or spec.schema_version != 4 or not target_markers <= markers
                    or not direct_player_amount_target_is_closed(target_schema)
                    or target_schema.get("player_relation") != "opponent"
                ):
                    return ()
                targeted = target_hand_count_dependencies(effects, target_schema, markers)
            elif spec.origin not in {ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.HISTORY, ScalarAmountOrigin.PUBLIC_AGGREGATE}:
                return ()
        else:
            return ()
    except (TypeError, ValueError):
        return ()
    if marker not in markers or (target_schema is not None and not targeted):
        return ()
    if markers - (target_markers if targeted else set()) - {'public-quantity-mana', marker, 'variable-resolution-mana-choice', 'generated_oracle_ir', 'spell_resolution',
                  'activated_ability', 'triggered_ability', 'cr-603-handling-triggered-abilities','fixed-typed-event-effect-trigger'}:
        return ()
    producer = 'quantity_expression.public_query_effect_amount' if marker == 'public-query-effect-amount' else 'quantity_expression.scalar_effect_amount'
    trigger = ('trigger.placement.apnap',) if 'cr-603-handling-triggered-abilities' in markers else ()
    return (PUBLIC_QUANTITY_MANA_CAPABILITY, producer, *characteristic_quantity_capabilities(value), *trigger, *targeted,
        *((RESOLUTION_MANA_CHOICE_CAPABILITY,VARIABLE_RESOLUTION_MANA_CAPABILITY) if choice else ()))


def fixed_then_named_graveyard_mana_capabilities(*, effects, target_schema, mechanic_ids):
    """Own a fixed mana instruction followed by one live named graveyard count."""
    from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from ..object_predicate import ObjectQuerySpec
    from .fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities

    fixed_marker = "fixed-resolution-mana"
    quantity_markers = {"public-quantity-mana", "public-query-effect-amount"}
    required = {fixed_marker, *quantity_markers, "closed-effect-program"}
    mechanics = set(mechanic_ids)
    if (target_schema is not None or len(effects) != 2 or not required <= mechanics
            or mechanics - (required | {"generated_oracle_ir", "spell_resolution"})):
        return ()
    first, counted = effects
    if (first.get("op") != "mana" or counted.get("op") != "mana"
            or "spend_restriction" in first or first.get("color") != counted.get("color")):
        return ()
    try:
        spec = PublicQueryAmountSpec.from_dict(counted.get("amount"))
    except (TypeError, ValueError, KeyError):
        return ()
    quantity = spec.quantity
    if (not isinstance(quantity, CharacteristicQuantitySpec) or not quantity.literal_name
            or quantity.scope is not CharacteristicQuantityScope.ALL_ZONES
            or quantity.query != ObjectQuerySpec(zones=("graveyard",), token=False)):
        return ()
    fixed = fixed_resolution_mana_capabilities(
        effects=(first,), target_schema=None, mechanic_ids=(fixed_marker,),
    )
    named = public_quantity_mana_node_capabilities(
        effects=(counted,), target_schema=None, mechanic_ids=quantity_markers,
    )
    return tuple(sorted({*fixed, *named})) if fixed and named else ()


def is_closed_public_quantity_mana_program(program):
    required = public_quantity_mana_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
