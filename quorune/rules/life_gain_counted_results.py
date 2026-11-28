"""Closed life-gain event counters around the existing typed mana-payment owner."""
from collections.abc import Mapping
from ..fixed_effect_payment import FixedEffectPaymentSpec, FIXED_EFFECT_PAYMENT_CAPABILITY
from ..counter_names import normalized_counter_name
from ..scalar_effect_amount_model import ScalarEffectAmountSpec, ScalarAmountOrigin
from ..replacement.immutable import thaw_value

LIFE_GAIN_COUNTER_RESULT_FIELD = 'life_gain_counter_result_version'
_TARGET = {'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1, 'types_any': ['creature']}
_CONDITION = {'field': 'player', 'op': 'eq', 'value': '$source.controller'}


def life_gain_counter_payment_projection(effect, *, resolved=False):
    fields = {'op', 'schema_version', 'player', 'payment', 'cost', 'effects', LIFE_GAIN_COUNTER_RESULT_FIELD}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect.get('op') != 'offer_optional_mana_payment'
            or type(effect.get('schema_version')) is not int or effect['schema_version'] != 2
            or type(effect.get(LIFE_GAIN_COUNTER_RESULT_FIELD)) is not int or effect[LIFE_GAIN_COUNTER_RESULT_FIELD] != 1):
        raise ValueError('Life-gain counter payment requires its complete version-one result marker')
    if (not resolved and effect['player'] != '$controller'
            or resolved and (type(effect['player']) is not str or not effect['player'])):
        raise ValueError('Life-gain counter payment lost its controller')
    payment = FixedEffectPaymentSpec.from_dict(effect['payment'])
    if payment.kind != 'mana' or not isinstance(effect['cost'], Mapping) or dict(effect['cost']) != dict(payment.requirements):
        raise ValueError('Life-gain counter result requires its positive ordinary mana price')
    children = effect['effects']
    if not isinstance(children, (tuple, list)) or len(children) != 1 or not isinstance(children[0], Mapping):
        raise ValueError('Life-gain payment requires one counter instruction')
    child = children[0]
    if (set(child) != {'op', 'card', 'counter', 'amount', 'source'} or child.get('op') != 'place_counters'
            or type(child.get('counter')) is not str or normalized_counter_name(child['counter']) != child['counter']):
        raise ValueError('Life-gain payment requires its independently owned counter result')
    if resolved:
        if (type(child['amount']) is not int or child['amount'] <= 0
                or any(type(child[key]) is not str or not child[key] for key in ('card', 'source'))):
            raise ValueError('Resolved life-gain counters require the sealed positive amount and object references')
    elif (child['card'] != '$target.0' or child['source'] != '$source'
            or ScalarEffectAmountSpec.from_dict(child['amount']) != ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)):
        raise ValueError('Life-gain counter result lost its original target or committed event amount')
    return {key: thaw_value(value) for key, value in effect.items() if key != LIFE_GAIN_COUNTER_RESULT_FIELD}


def life_gain_counter_payment_node_capabilities(*, effects, target_schema, mechanic_ids=(), **kwargs):
    if (len(effects) != 1 or target_schema != _TARGET or type(target_schema.get('count')) is not int):
        return ()
    try:
        projected = life_gain_counter_payment_projection(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    from .capabilities import capability_dependencies_for_node
    base = capability_dependencies_for_node(effects=tuple(projected['effects']), target_schema=target_schema,
        mechanic_ids=('scalar-effect-amount', 'declared-effect-amount', 'cr-122-counters', 'cr-115-targets'))
    return tuple(sorted({*base, FIXED_EFFECT_PAYMENT_CAPABILITY, 'trigger.event.normalized_life_gain',
        'counter.producer.fixed_event_trigger'})) if base else ()


def _has_life_counter_payment(value):
    if isinstance(value, Mapping):
        if LIFE_GAIN_COUNTER_RESULT_FIELD in value:
            return True
        if value.get('op') == 'offer_optional_mana_payment' and value.get('schema_version') == 2:
            for child in value.get('effects', ()):
                if (isinstance(child, Mapping) and child.get('op') == 'place_counters'
                        and isinstance(child.get('amount'), Mapping)
                        and child['amount'].get('origin') == ScalarAmountOrigin.EVENT_AMOUNT.value):
                    return True
        return any(_has_life_counter_payment(v) for v in value.values())
    return isinstance(value, (tuple, list)) and any(_has_life_counter_payment(v) for v in value)


def life_gain_counter_payment_program_dependencies(program):
    if not _has_life_counter_payment((program.effects, getattr(program, 'handlers', ()),
            program.target_schema, getattr(program, 'cost_schema', None))):
        return ()
    if (program.event != 'life.gained' or program.active_zone != 'battlefield'
            or thaw_value(program.event_condition) != _CONDITION or getattr(program, 'cost_schema', None) is not None):
        raise ValueError('Paid life-gain counters require their exact controller life-gain frame')
    required = life_gain_counter_payment_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if not required:
        raise ValueError('Paid life-gain counters require their complete marked price and result')
    return required


CAPPED_LIFE_GAIN_DRAW_VERSION = 17
_CAPPED_MANA_DRAW = "life_gain_capped_generic_draw"


def capped_life_gain_draw_effect():
    return {
        "op": "offer_optional_mana_payment", "schema_version": CAPPED_LIFE_GAIN_DRAW_VERSION,
        "player": "$controller", "payment": {"kind": _CAPPED_MANA_DRAW, "limit": "$context.amount"},
    }


def uses_capped_life_gain_draw(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != "offer_optional_mana_payment":
        return False
    payment = effect.get("payment")
    return (effect.get("schema_version") == CAPPED_LIFE_GAIN_DRAW_VERSION
            or isinstance(payment, Mapping) and payment.get("kind") == _CAPPED_MANA_DRAW)


def capped_life_gain_draw_limit(effect, *, payer=None):
    compiled = capped_life_gain_draw_effect()
    if payer is None:
        if (not uses_capped_life_gain_draw(effect) or type(effect.get("schema_version")) is not int
                or thaw_value(effect) != compiled):
            raise ValueError("Capped life-gain payment requires its original event amount")
        return
    payment = effect.get("payment")
    amount = payment.get("limit") if isinstance(payment, Mapping) else None
    if (type(payer) is not str or not payer or type(amount) is not int or amount <= 0
            or type(effect.get("schema_version")) is not int or thaw_value(effect) != {
                **compiled, "player": payer, "payment": {"kind": _CAPPED_MANA_DRAW, "limit": amount},
            }):
        raise ValueError("Capped life-gain payment requires a positive committed amount and its payer")
    return amount


def capped_life_gain_draw_node_capabilities(*, effects, target_schema):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        capped_life_gain_draw_limit(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return (
        FIXED_EFFECT_PAYMENT_CAPABILITY, "zone.draw.library_to_hand",
        "trigger.event.normalized_life_gain", "trigger.effect.fixed_event", "trigger.placement.apnap",
    )


def capped_life_gain_draw_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(uses_capped_life_gain_draw(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = capped_life_gain_draw_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("life.gained", "battlefield")
            or thaw_value(program.event_condition) != _CONDITION or program.handlers
            or program.cost_schema is not None or not program.ability_id.startswith("trigger:")):
        raise ValueError("Capped life-gain payment requires its complete controller life-gain trigger")
    return required


LIFE_GAIN_LIFE_DRAW_VERSION = 20
LIFE_GAIN_LIFE_DRAW_CAPABILITY = "effect.choice.life_gain_amount_life_draw"
_LIFE_DRAW_KIND = "life_gain_equal_life_draw"


def life_gain_life_draw_effect():
    return {
        "op": "offer_optional_mana_payment", "schema_version": LIFE_GAIN_LIFE_DRAW_VERSION,
        "player": "$controller", "payment": {"kind": _LIFE_DRAW_KIND, "amount": "$context.amount"},
    }


def uses_life_gain_life_draw(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != "offer_optional_mana_payment":
        return False
    payment = effect.get("payment")
    return (effect.get("schema_version") == LIFE_GAIN_LIFE_DRAW_VERSION
            or isinstance(payment, Mapping) and payment.get("kind") == _LIFE_DRAW_KIND)


def life_gain_life_draw_amount(effect, payer):
    if not isinstance(effect, Mapping):
        raise ValueError("Life-gain payment requires its closed instruction")
    payment = effect.get("payment")
    amount = payment.get("amount") if isinstance(payment, Mapping) else None
    expected = {**life_gain_life_draw_effect(), "player": payer,
                "payment": {"kind": _LIFE_DRAW_KIND, "amount": amount}}
    if (type(payer) is not str or not payer or type(amount) is not int or amount <= 0
            or type(effect.get("schema_version")) is not int or thaw_value(effect) != expected):
        raise ValueError("Life-gain payment requires its original controller and positive gain amount")
    return amount


def life_gain_life_draw_node_capabilities(*, effects, target_schema):
    if (target_schema is not None or len(effects) != 1
            or not uses_life_gain_life_draw(effects[0])
            or type(effects[0].get("schema_version")) is not int
            or thaw_value(effects[0]) != life_gain_life_draw_effect()):
        return ()
    return (
        LIFE_GAIN_LIFE_DRAW_CAPABILITY, FIXED_EFFECT_PAYMENT_CAPABILITY,
        "zone.draw.library_to_hand", "trigger.event.normalized_life_gain",
        "trigger.effect.fixed_event", "trigger.placement.apnap",
    )


def life_gain_life_draw_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(uses_life_gain_life_draw(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = life_gain_life_draw_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("life.gained", "battlefield")
            or thaw_value(program.event_condition) != _CONDITION or program.handlers
            or program.cost_schema is not None or not program.ability_id.startswith("trigger:")):
        raise ValueError("Life-gain life payment requires its original controller gain trigger")
    return required
