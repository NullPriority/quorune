"""Closed mana payment and permanent sacrifice for a represented spell caster."""

from collections.abc import Mapping

from .compiler.affected_player_sacrifice_templates import (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
    FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
    AffectedPlayerSacrificeSubject,
    FixedAffectedPlayerSacrificeTemplate,
)
from .fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from .object_predicate import ObjectQuerySpec
from .replacement.immutable import FrozenMap, thaw_value
from .rules.effect_shape_walkers import nested_effects


SPELL_CASTER_SACRIFICE_PAYMENT_VERSION = 14
SPELL_CASTER_SACRIFICE_PAYMENT_MECHANICS = (
    FIXED_EFFECT_PAYMENT_MECHANIC, FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice",
)
_OPERATION = "offer_optional_mana_payment"
_PRICE = FixedEffectPaymentSpec(
    "mana", requirements=FrozenMap({"GENERIC": 1, **dict.fromkeys("WUBRGC", 0)}),
)
_PERMANENT = ObjectQuerySpec(zones=("battlefield",))
_SACRIFICE = FixedAffectedPlayerSacrificeTemplate(AffectedPlayerSacrificeSubject.EACH_PLAYER, 1, _PERMANENT)


def spell_caster_sacrifice_cast_condition_is_closed(condition):
    return thaw_value(condition) in (
        {"field": "types", "op": "contains_any", "value": ["creature"]},
        {"field": "controller", "op": "ne", "value": "$source.controller"},
    )


def spell_caster_sacrifice_payment_effect(*, player="$context.player", controller="$controller"):
    return {
        "op": _OPERATION, "schema_version": SPELL_CASTER_SACRIFICE_PAYMENT_VERSION,
        "player": player, "payment": _PRICE.to_dict(), "cost": dict(_PRICE.requirements),
        "effects": [],
        "declined_effects": [{**_SACRIFICE.effects[0], "actor": controller, "players": [player]}],
    }


def uses_spell_caster_sacrifice_payment(effect):
    return isinstance(effect, Mapping) and effect.get("op") == _OPERATION and (
        effect.get("schema_version") == SPELL_CASTER_SACRIFICE_PAYMENT_VERSION
        or effect.get("player") == "$context.player" and "declined_effects" in effect
    )


def spell_caster_sacrifice_payment_spec(effect, *, player="$context.player", controller="$controller",
                                       continuation=False):
    if not isinstance(effect, Mapping) or any(type(seat) is not str or not seat for seat in (player, controller)):
        raise ValueError("Caster payment requires its represented payer and ability controller")
    raw = thaw_value(effect)
    if continuation:
        if (
            raw.pop("_choice_actor", None) != player
            or type(raw.pop("_stack_label", None)) is not str
            or raw.pop("_legal_objects", None) != []
        ):
            raise ValueError("Caster payment lost its original mana choice")
    if (
        raw != spell_caster_sacrifice_payment_effect(player=player, controller=controller)
        or type(raw["schema_version"]) is not int
        or any(type(amount) is not int for amount in raw["cost"].values())
        or type(raw["declined_effects"][0]["count"]) is not int
        or ObjectQuerySpec.from_dict(raw["declined_effects"][0]["predicate"]) != _PERMANENT
        or FixedEffectPaymentSpec.from_dict(raw["payment"]) != _PRICE
    ):
        raise ValueError("Caster payment requires the exact generic-one price and fixed permanent sacrifice")
    return _PRICE


def spell_caster_sacrifice_payment_capabilities(effect):
    cost = spell_caster_sacrifice_payment_spec(effect)
    return tuple(sorted({
        *cost.capabilities, FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
        "trigger.event.normalized_spell_cast", "trigger.placement.apnap", "zone.change.destination_replacement",
    }))


def spell_caster_sacrifice_payment_program_dependencies(program):
    effects = (*program.effects, *getattr(program, "handlers", ()))
    if not any(uses_spell_caster_sacrifice_payment(effect) for effect in nested_effects(effects)):
        return ()
    if (
        (program.event, program.active_zone) != ("spell.cast", "battlefield")
        or not spell_caster_sacrifice_cast_condition_is_closed(program.event_condition)
        or program.target_schema is not None or getattr(program, "cost_schema", None) is not None
        or len(program.effects) != 1 or getattr(program, "handlers", ())
    ):
        raise ValueError("Caster payment requires the complete represented battlefield cast trigger")
    return spell_caster_sacrifice_payment_capabilities(program.effects[0])
