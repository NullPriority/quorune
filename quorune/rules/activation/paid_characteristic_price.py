"""Closed selected-creature price shared by compiler and activation rules."""
from collections.abc import Mapping
from ...activation_cost_choice import CostChoice
from ...replacement.immutable import FrozenMap


def source_sacrifice_power_price_is_closed(cost):
    """Require one ordinary tap and self-sacrifice with a positive fixed mana price."""
    flags = {
        "tap_source": True, "sacrifice_source": True,
        "untap_source": False, "discard_source": False, "exile_source": False,
    }
    empty = {"complex_symbols", "choices", "uncompiled_costs"}
    zero = {"life_payment", "energy_payment"}
    absent = {"loyalty_delta", "source_counter_removal_cost"}
    if not isinstance(cost, Mapping) or set(cost) != set(flags) | empty | zero | absent | {"text", "mana"}:
        return False
    mana = cost["mana"]
    return (
        isinstance(mana, Mapping) and set(mana) == set("WUBRGC") | {"GENERIC"}
        and all(type(amount) is int and amount >= 0 for amount in mana.values()) and any(mana.values())
        and all(cost[name] is flag for name, flag in flags.items())
        and all(isinstance(cost[name], (list, tuple)) and not cost[name] for name in empty)
        and all(type(cost[name]) is int and cost[name] == 0 for name in zero)
        and all(cost[name] is None for name in absent)
        and type(cost["text"]) is str and bool(cost["text"])
    )


def activation_creature_sacrifice_choice(cost, *, card_type="creature"):
    if not isinstance(cost, Mapping):
        choices = getattr(cost, 'choices', None)
        if not isinstance(choices, (tuple, list)) or any(not isinstance(c, CostChoice) for c in choices):
            return None
        cost = {**{k: getattr(cost, k, None) for k in ('uncompiled_costs', 'complex_symbols', 'mana', 'tap_source',
            'discard_source', 'sacrifice_source', 'exile_source', 'untap_source', 'life_payment',
            'energy_payment', 'source_counter_removal_cost', 'loyalty_delta', 'waterbend',
            'random_discard_payment', 'announced_x_symbols')}, 'choices': [c.compact() for c in choices]}
    if not isinstance(cost, Mapping) or cost.get('uncompiled_costs') or cost.get('complex_symbols'):
        return None
    mana = cost.get('mana')
    if (type(cost.get('tap_source')) is not bool or not isinstance(mana, Mapping)
            or set(mana) - {'W', 'U', 'B', 'R', 'G', 'C', 'GENERIC'}
            or any(type(n) is not int or n < 0 for n in mana.values())):
        return None
    if any(cost.get(k) is not False for k in ('discard_source', 'sacrifice_source', 'exile_source', 'untap_source')):
        return None
    if any(cost.get(k) for k in ('life_payment', 'energy_payment', 'source_counter_removal_cost', 'loyalty_delta',
            'waterbend', 'random_discard_payment', 'announced_x_symbols')):
        return None
    choices = cost.get('choices')
    if not isinstance(choices, (list, tuple)) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        return None
    value = choices[0]
    if set(value) - {'k', 'n', 'z', 'q', 'other'} or not {'k', 'n', 'z', 'q'} <= set(value):
        return None
    if type(value.get('other', 0)) is not int or value.get('other', 0) not in (0, 1):
        return None
    try:
        choice = CostChoice(value['k'], value['n'], value['z'], another=bool(value.get('other')),
            predicate=FrozenMap(value['q']))
        price = choice.fixed_zone_change_cost()
    except (TypeError, ValueError, KeyError):
        return None
    if (price is None or price.operation != 'sacrifice_one' or choice.count != 1
            or not (card_type in price.predicate.types_all or price.predicate.types_any == (card_type,))):
        return None
    return choice

PAID_ENCHANTMENT_MANA_VALUE_CONTEXT = "paid_activation_enchantment_mana_value"


def activation_enchantment_sacrifice_choice(cost):
    from ...object_predicate import ObjectQuerySpec

    choice = activation_creature_sacrifice_choice(cost, card_type="enchantment")
    if (choice is None or not choice.another or cost["tap_source"]
            or {name: amount for name, amount in cost["mana"].items() if amount} != {"GENERIC": 2, "B": 1}
            or choice.fixed_zone_change_cost().predicate != ObjectQuerySpec(
                zones=("battlefield",), controller="$actor", types_any=("enchantment",), known_to_actor=True)):
        return None
    return choice


def paid_enchantment_pump_effect():
    from ...scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    amount = ScalarEffectAmountSpec(
        ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE, characteristic="mana_value", schema_version=12,
    ).to_dict()
    return {
        "op": "apply_source_characteristics_until_end_of_turn", "card": "$source.zone_object",
        "set_card_types": None, "set_subtypes": None, "set_colors": None,
        "base_power": None, "base_toughness": None, "power": amount, "toughness": 0, "keywords": [],
    }


def activation_enchantment_pump_is_exact(*, effects, cost_schema, target_schema):
    from ...replacement.immutable import thaw_value

    return (target_schema is None and tuple(thaw_value(effects)) == (paid_enchantment_pump_effect(),)
            and activation_enchantment_sacrifice_choice(cost_schema) is not None)


def _ordinary_paid_creature_choice(cost_schema):
    from ...object_predicate import ObjectQuerySpec

    choice = activation_creature_sacrifice_choice(cost_schema)
    if choice is None or choice.fixed_zone_change_cost().predicate != ObjectQuerySpec(
        zones=("battlefield",), controller="$actor", types_any=("creature",), known_to_actor=True,
    ):
        return None
    return choice


def activation_paid_creature_choice(cost_schema, *, legendary=False):
    choice = _ordinary_paid_creature_choice(cost_schema)
    if (choice is None or choice.another is not legendary or cost_schema["tap_source"] is not legendary
            or any(cost_schema["mana"].values())):
        return None
    return choice


def activation_paid_suspected_choice(cost_schema):
    choice = _ordinary_paid_creature_choice(cost_schema)
    if (choice is None or not choice.another or cost_schema["tap_source"]
            or {key: value for key, value in cost_schema["mana"].items() if value} != {"GENERIC": 2, "B": 1}):
        return None
    return choice
