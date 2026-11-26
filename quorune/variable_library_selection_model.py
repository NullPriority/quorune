from __future__ import annotations

"""Resolved-count bridge to the existing fixed library partition owner."""

from collections.abc import Mapping
from copy import deepcopy

from .replacement.immutable import FrozenMap
from .spell_mana_conditions import validate_actual_cast_mana_total_reference
from .spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY

VARIABLE_LIBRARY_SELECTION_OPERATION = 'variable_library_selection'
VARIABLE_LIBRARY_SELECTION_MECHANIC = 'variable-library-selection'
VARIABLE_LIBRARY_SELECTION_CAPABILITY = 'library.select.variable_controller'
ALL_LOOKED = 'all_looked'
DOMAIN_TOP_SELECTION_VERSION = 2
VARIABLE_UP_TO_TWO_HAND_SELECTION_VERSION = 3
ACTUAL_MANA_SPENT_LIBRARY_SELECTION_VERSION = 4
DEVOTION_LIBRARY_WIN_VERSION = 5
DEVOTION_LIBRARY_WIN_CAPABILITY = "library.select.devotion_top_then_win"
DEVOTION_LIBRARY_WIN_TEMPLATE = "devotion-library-top-then-win-v1"
_FIELDS = {'op','schema_version','player','look_count','public_reveal','selected_reveal',
    'selection_policy','minimum','maximum','predicate_groups','remainder_destination','remainder_order'}


def _amount(value,*,resolved):
    if type(value) is int:
        if value < 0:raise ValueError('Library inspection count must be nonnegative')
        return
    if resolved or not isinstance(value,Mapping):
        raise ValueError('Library inspection count must be resolved before looking')
    from .query_effect_amount_model import (PublicQueryAmountSpec,CastXAmountSpec,
        PUBLIC_QUERY_AMOUNT_KIND,CAST_X_AMOUNT_KIND)
    from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
    constructor={PUBLIC_QUERY_AMOUNT_KIND:PublicQueryAmountSpec,CAST_X_AMOUNT_KIND:CastXAmountSpec,
        SCALAR_AMOUNT_KIND:ScalarEffectAmountSpec}.get(value.get('kind'))
    if constructor is None or constructor.from_dict(value).coefficient < 0:
        raise ValueError('Library inspection amount producer is unrepresented')


def domain_top_selection_effect(count, *, player="$controller"):
    from .rules.library_selection_capability_shapes import fixed_top_random_bottom_selection

    return {
        **fixed_top_random_bottom_selection(count, player=player),
        "op": VARIABLE_LIBRARY_SELECTION_OPERATION,
        "schema_version": DOMAIN_TOP_SELECTION_VERSION,
    }


def _validate_domain_top_selection(effect, *, resolved):
    from .query_effect_amount_model import PublicQueryAmountSpec
    from .replacement.immutable import thaw_value
    from .resolution_conditions import CONTROLLER_BASIC_LAND_TYPES

    count = effect["look_count"]
    if type(count) is int:
        if not 0 <= count <= 5:
            raise ValueError("The domain inspection count is outside the five basic land types")
    elif resolved or not isinstance(count, Mapping):
        raise ValueError("Domain inspection requires a resolved land-type count")
    else:
        spec = PublicQueryAmountSpec.from_dict(count)
        if spec.schema_version != 2 or spec.coefficient != 1 or spec.quantity != CONTROLLER_BASIC_LAND_TYPES:
            raise ValueError("Domain inspection requires one declared current basic land subtype count")
    if (any(type(effect.get(field)) is not int for field in ("minimum", "maximum"))
            or effect.get("public_reveal") is not False or effect.get("selected_reveal") is not False
            or thaw_value(effect) != domain_top_selection_effect(thaw_value(count), player=effect["player"])):
        raise ValueError("Domain inspection must delegate to the exact private top partition")


def variable_up_to_two_hand_selection_effect(count, *, player="$controller", remainder_destination):
    from .rules.library_selection_capability_shapes import fixed_up_to_two_hand_selection

    return {
        **fixed_up_to_two_hand_selection(2, player=player, remainder_destination=remainder_destination),
        "op": VARIABLE_LIBRARY_SELECTION_OPERATION,
        "schema_version": VARIABLE_UP_TO_TWO_HAND_SELECTION_VERSION,
        "look_count": count,
    }


def _validate_variable_up_to_two_hand_selection(effect, *, resolved):
    from .query_effect_amount_model import CastXAmountSpec, PublicQueryAmountSpec
    from .replacement.immutable import thaw_value
    from .resolution_conditions import CONTROLLER_BASIC_LAND_TYPES

    count = effect["look_count"]
    destination = effect["remainder_destination"]
    if (destination not in ("graveyard", "library_bottom")
            or any(type(effect[field]) is not int for field in ("minimum", "maximum"))
            or effect["public_reveal"] is not False or effect["selected_reveal"] is not False
            or thaw_value(effect) != variable_up_to_two_hand_selection_effect(
                thaw_value(count), player=effect["player"], remainder_destination=destination)):
        raise ValueError("Variable hand selection has one exact private choice and remainder")
    if type(count) is int:
        if count < 0 or destination == "graveyard" and count > 5:
            raise ValueError("The resolved inspection count is outside its represented range")
        return
    if resolved or not isinstance(count, Mapping):
        raise ValueError("The private selection count must be resolved before looking")
    if destination == "graveyard":
        spec = PublicQueryAmountSpec.from_dict(count)
        if spec.schema_version != 2 or spec.coefficient != 1 or spec.quantity != CONTROLLER_BASIC_LAND_TYPES:
            raise ValueError("The graveyard result requires the declared live domain count")
    elif CastXAmountSpec.from_dict(count) != CastXAmountSpec():
        raise ValueError("The random-bottom result requires the one recorded announced X")


def uses_actual_mana_spent_library_selection(effect):
    return (
        isinstance(effect, Mapping) and effect.get("op") == VARIABLE_LIBRARY_SELECTION_OPERATION
        and effect.get("schema_version") == ACTUAL_MANA_SPENT_LIBRARY_SELECTION_VERSION
    )


def actual_mana_spent_library_selection_effect(count, *, player="$controller"):
    return {
        "op": VARIABLE_LIBRARY_SELECTION_OPERATION, "schema_version": ACTUAL_MANA_SPENT_LIBRARY_SELECTION_VERSION,
        "player": player, "look_count": count, "public_reveal": False, "selected_reveal": False,
        "selection_policy": "fixed_any", "minimum": 2, "maximum": 2, "predicate_groups": [],
        "remainder_destination": "library_bottom", "remainder_order": "random",
    }


def _validate_actual_mana_spent_library_selection(effect, *, resolved):
    count, actor = effect["look_count"], effect["player"]
    if resolved:
        if type(count) is not int or count < 0 or type(actor) is not str or not actor or actor.startswith("$"):
            raise ValueError("Actual mana spent and the resolving controller must be bound")
    else:
        validate_actual_cast_mana_total_reference(count)
        if actor != "$controller":
            raise ValueError("Actual spell payment must inspect the resolving controller's library")
    expected = actual_mana_spent_library_selection_effect(count, player=actor)
    if (
        any(type(effect.get(field)) is not int for field in ("schema_version", "minimum", "maximum"))
        or effect.get("public_reveal") is not False or effect.get("selected_reveal") is not False
        or FrozenMap(effect) != FrozenMap(expected)
    ):
        raise ValueError("Actual payment requires exactly two kept privately with the rest randomly bottomed")


def uses_devotion_library_win(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == VARIABLE_LIBRARY_SELECTION_OPERATION
            and effect.get("schema_version") == DEVOTION_LIBRARY_WIN_VERSION)


def _blue_devotion_amount():
    from .characteristic_fragments import CharacteristicQuantityReduction, CharacteristicQuantityScope, CharacteristicQuantitySpec
    from .object_predicate import ObjectQuerySpec
    from .query_effect_amount_model import PublicQueryAmountSpec

    quantity = CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE, query=ObjectQuerySpec(zones=("battlefield",)),
        schema_version=2, reduction=CharacteristicQuantityReduction.DEVOTION, mana_colors=("U",),
    )
    return PublicQueryAmountSpec(quantity).to_dict()


def devotion_library_win_effect(count=None, *, player="$controller", stage="inspect"):
    from .rules.library_selection_capability_shapes import fixed_top_random_bottom_selection

    return {
        **fixed_top_random_bottom_selection(_blue_devotion_amount() if count is None else count, player=player),
        "op": VARIABLE_LIBRARY_SELECTION_OPERATION, "schema_version": DEVOTION_LIBRARY_WIN_VERSION,
        "stage": stage,
    }


def _validate_devotion_library_win(effect, *, resolved):
    if not resolved:
        from .query_effect_amount_model import PublicQueryAmountSpec

        PublicQueryAmountSpec.from_dict(effect.get("look_count"))
        expected = devotion_library_win_effect()
    else:
        count, actor, stage = effect.get("look_count"), effect.get("player"), effect.get("stage")
        if (type(count) is not int or count < 0 or type(actor) is not str or not actor
                or actor.startswith("$") or stage not in ("inspect", "compare")):
            raise ValueError("Devotion selection requires the resolving controller and one measured count")
        expected = devotion_library_win_effect(count, player=actor, stage=stage)
    if (FrozenMap(effect) != FrozenMap(expected)
            or any(type(effect[field]) is not int for field in ("schema_version", "minimum", "maximum"))
            or effect["public_reveal"] is not False or effect["selected_reveal"] is not False):
        raise ValueError("Devotion selection requires the original private top selection and win comparison")


def validate_variable_library_selection(effect,*,resolved=False):
    if uses_devotion_library_win(effect):
        _validate_devotion_library_win(effect, resolved=resolved)
        return
    if uses_actual_mana_spent_library_selection(effect) and set(effect) == _FIELDS:
        _validate_actual_mana_spent_library_selection(effect, resolved=resolved)
        return
    if (isinstance(effect, Mapping) and effect.get("op") == VARIABLE_LIBRARY_SELECTION_OPERATION
            and type(effect.get("schema_version")) is int and effect["schema_version"] == VARIABLE_UP_TO_TWO_HAND_SELECTION_VERSION
            and set(effect) == _FIELDS
            and type(effect["player"]) is str and effect["player"] and effect["player"].strip() == effect["player"]):
        _validate_variable_up_to_two_hand_selection(effect, resolved=resolved)
        return
    if (isinstance(effect, Mapping) and effect.get("op") == VARIABLE_LIBRARY_SELECTION_OPERATION
            and type(effect.get("schema_version")) is int and effect["schema_version"] == DOMAIN_TOP_SELECTION_VERSION
            and set(effect) == _FIELDS | {"selected_destination"}
            and type(effect["player"]) is str and effect["player"] and effect["player"].strip() == effect["player"]):
        _validate_domain_top_selection(effect, resolved=resolved)
        return
    if (not isinstance(effect,Mapping) or set(effect)!=_FIELDS
            or effect.get('op')!=VARIABLE_LIBRARY_SELECTION_OPERATION
            or type(effect['schema_version']) is not int or effect['schema_version']!=1
            or type(effect['player']) is not str or not effect['player'] or effect['player'].strip()!=effect['player']):
        raise ValueError('Variable library selection has a closed schema')
    _amount(effect['look_count'],resolved=resolved)
    minimum,maximum=effect['minimum'],effect['maximum']
    dynamic=maximum==ALL_LOOKED
    if (type(minimum) is not int or minimum<0
            or not dynamic and (type(maximum) is not int or maximum<=0 or minimum>maximum)
            or dynamic and (minimum!=0 or effect['selection_policy'] not in {'up_to_matching','all_matching'})):
        raise ValueError('Variable library selection cardinality is malformed')
    groups=effect['predicate_groups']
    if effect['selection_policy']=='optional_slots' and (not isinstance(groups,(list,tuple)) or len(groups)!=2):
        raise ValueError('Variable optional selection represents two independently typed slots')
    proxy_count=max(1,minimum,maximum if not dynamic else 1)
    proxy={key:deepcopy(value) for key,value in effect.items() if key!='schema_version'}
    proxy.update(op='fixed_library_selection',player='$controller',look_count=proxy_count,
        maximum=proxy_count if dynamic else maximum)
    from .rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
    if not fixed_library_selection_node_capabilities(effects=(proxy,),target_schema=None,
            mechanic_ids={'fixed-library-selection'}):
        raise ValueError('Variable selection does not have a complete existing fixed selection owner')


def fixed_selection_for_resolved_count(effect):
    """CR609.3: normalize before inspection; zero never reaches a look owner."""
    validate_variable_library_selection(effect,resolved=True)
    count=effect['look_count']
    if count==0:return None
    if uses_devotion_library_win(effect):
        from .rules.library_selection_capability_shapes import (
            RESOLVED_TOP_RANDOM_BOTTOM_VERSION, fixed_top_random_bottom_selection,
        )

        return fixed_top_random_bottom_selection(count, player=effect["player"], version=RESOLVED_TOP_RANDOM_BOTTOM_VERSION)
    if effect["schema_version"] == VARIABLE_UP_TO_TWO_HAND_SELECTION_VERSION:
        from .rules.library_selection_capability_shapes import fixed_up_to_two_hand_selection

        return fixed_up_to_two_hand_selection(
            count, player=effect["player"], remainder_destination=effect["remainder_destination"],
        )
    if effect["schema_version"] == DOMAIN_TOP_SELECTION_VERSION:
        from .rules.library_selection_capability_shapes import fixed_top_random_bottom_selection

        return fixed_top_random_bottom_selection(count, player=effect["player"])
    result={key:deepcopy(value) for key,value in effect.items() if key!='schema_version'}
    result['op']='fixed_library_selection'
    result['minimum']=min(result['minimum'],count)
    result['maximum']=count if result['maximum']==ALL_LOOKED else min(result['maximum'],count)
    if result['selection_policy']=='optional_slots' and count==1:
        alternatives=[]
        for group in result['predicate_groups']:
            for predicate in group:
                if predicate not in alternatives:alternatives.append(predicate)
        result.update(selection_policy='up_to_matching',predicate_groups=[alternatives],minimum=0,maximum=1)
    return result


def variable_library_selection_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op')!=VARIABLE_LIBRARY_SELECTION_OPERATION:return ()
    validate_variable_library_selection(value)
    exact = (VARIABLE_LIBRARY_SELECTION_CAPABILITY, 'library.select.fixed_controller')
    if uses_devotion_library_win(value):
        return (*exact, DEVOTION_LIBRARY_WIN_CAPABILITY, "state_query.devotion.current",
                "quantity_expression.public_query_effect_amount", "effect.player.game_win")
    return (*exact, MANA_SPENT_COUNTS_CAPABILITY) if uses_actual_mana_spent_library_selection(value) else exact
