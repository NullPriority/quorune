"""One original opposing caster may discard a card to avoid fixed life loss."""

from collections.abc import Mapping

from ..apnap_object_payment import APNAP_OBJECT_PAYMENT_CAPABILITY, DECLINED_LIFE_LOSS
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


CASTER_DISCARD_PAYMENT_CAPABILITY = "choice.apnap.original_caster_discard_or_life_loss"
CASTER_DISCARD_PAYMENT_FIELD = "original_caster_discard_payment_version"
CASTER_DISCARD_PAYMENT_MECHANICS = (
    "fixed-affected-player-discard", "cr-402-hand",
)
_ORIGINAL_CASTER = "$context.controller"
_OPPONENT_CAST = FrozenMap({"field": "controller", "op": "ne", "value": "$source.controller"})
_HAND = ObjectQuerySpec(zones=("hand",))


def opponent_cast_discard_condition(condition):
    return isinstance(condition, Mapping) and FrozenMap(condition) == _OPPONENT_CAST


def uses_caster_discard_payment(effect):
    return isinstance(effect, Mapping) and CASTER_DISCARD_PAYMENT_FIELD in effect


def caster_discard_payment_effect(amount, *, payer=_ORIGINAL_CASTER, controller="$controller"):
    if type(amount) is not int or amount <= 0:
        raise ValueError("The original caster needs a fixed positive life loss")
    return {
        "op": "choose_cards_apnap", "actor": controller, "players": [payer],
        "zone": "hand", "predicate": _HAND.canonical_dict(), "count": 1, "then": "discard", "hidden": True,
        "prompt": f"Choose to discard a card or lose {amount} life.",
        DECLINED_LIFE_LOSS: amount, CASTER_DISCARD_PAYMENT_FIELD: 1,
    }


def validate_caster_discard_payment(effect, *, payer=_ORIGINAL_CASTER, controller="$controller"):
    if not uses_caster_discard_payment(effect) or type(payer) is not str or not payer or payer == controller:
        raise ValueError("The original opposing caster and the trigger controller must be distinct")
    if (type(effect.get(CASTER_DISCARD_PAYMENT_FIELD)) is not int or type(effect.get("count")) is not int
            or effect.get("hidden") is not True or FrozenMap(effect) != FrozenMap(caster_discard_payment_effect(
                effect.get(DECLINED_LIFE_LOSS), payer=payer, controller=controller,
            ))):
        raise ValueError("The original caster payment must keep its exact private hand and loss")


def caster_discard_payment_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or not set(CASTER_DISCARD_PAYMENT_MECHANICS).issubset(mechanic_ids)
            or not uses_caster_discard_payment(effects[0])):
        return ()
    try:
        validate_caster_discard_payment(effects[0])
    except (TypeError, ValueError):
        return ()
    return (
        CASTER_DISCARD_PAYMENT_CAPABILITY, APNAP_OBJECT_PAYMENT_CAPABILITY,
        "choice.affected_player.fixed_discard", "life.change.effect", "trigger.event.normalized_spell_cast",
        "trigger.placement.apnap", "zone.change.destination_replacement",
    )


def caster_discard_payment_program_dependencies(program):
    effects = tuple(program.effects)
    if not any(uses_caster_discard_payment(effect) for effect in nested_effects((*effects, *program.handlers))):
        return ()
    required = caster_discard_payment_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if ((program.event, program.active_zone) != ("spell.cast", "battlefield")
            or not opponent_cast_discard_condition(program.event_condition)
            or program.handlers or program.cost_schema is not None or not required):
        raise ValueError("Caster discard payment requires its complete original opponent cast trigger")
    return required


def original_caster_discard_payer(item, effect):
    if (item is None or item.kind != "triggered_ability" or item.context.get("event") != "spell.cast"
            or item.context.get("currently_resolving") is not True or item.targets):
        raise ValueError("Caster discard requires the original resolving untargeted cast trigger")
    payer = item.context.get("controller")
    validate_caster_discard_payment(effect, payer=payer, controller=item.controller)
    return payer
