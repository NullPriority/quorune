"""Collect resolved defender payments alongside ordinary declaration costs."""

from collections.abc import Mapping

from ..continuous_effect_model import Layer
from ..continuous_effect_state import commit_continuous_effect
from ..declaration_costs import DeclarationCost
from ..protected_attack_payment_model import (
    CAPABILITY, MECHANIC, OPERATION, ResolvedProtectedAttackPayment,
    decode_protected_attack_payment,
)
from ..replacement.immutable import FrozenMap, thaw_value


def apply_protected_attack_payment(host, intent):
    host._require_seat(intent.actor, in_game=True)
    effect = ResolvedProtectedAttackPayment(
        host._next_ref("AC"), intent.source_id, host._next_zone_timestamp(), intent.actor, intent.cost,
    )
    commit_continuous_effect(host.state, effect)
    host._log(intent.actor, "combat.attack.payment.created",
              "Attacks against the protected player or their planeswalkers require payment.",
              {"player": intent.actor, "effect_id": effect.effect_id, "mana": dict(intent.cost.mana)}, importance=1)
    return effect.effect_id


def source_controls_planeswalker(host, target, controller):
    if target is None or target.controller != controller:
        return False
    types, _, _ = host._type_parts(str(host._effective_card_data(target).get("type_line") or ""))
    return "planeswalker" in types


def declaration_mana_cost(host, source, template):
    """Derive this source's cost from effective battlefield characteristics."""
    if template.mana_calculation is None:
        return template.mana, template.printed_cost
    enchantment_count = sum(
        "enchantment" in host._type_parts(
            str(host._effective_card_data(card, maximum_layer=Layer.TYPE).get("type_line") or "")
        )[0]
        for card in host.state.cards.values()
        if card.zone == "battlefield" and not card.phased_out and card.controller == source.controller
    )
    mana = (("GENERIC", enchantment_count),) if enchantment_count else ()
    return mana, f"{{{enchantment_count}}}"


def protected_attack_payment_costs(host, *, kind, payer, domains, by_ref):
    if kind != "attack":
        return ()
    return tuple(
        DeclarationCost(
            cost_id=f"attack-cost:resolution:{effect.effect_id}:{attacker}:{option}",
            variable=attacker, option=option, payer=payer, mana=effect.cost.mana, source=effect.effect_id,
            label=f"Attacking {option} with {attacker} requires {effect.cost.printed_cost}.",
        )
        for effect in host.state.continuous_effects or ()
        if isinstance(effect, ResolvedProtectedAttackPayment)
        for attacker, options in sorted(domains.items())
        for option in options
        if option == effect.expires_on_next_turn_of
        or source_controls_planeswalker(host, by_ref.get(option), effect.expires_on_next_turn_of)
    )


def protected_attack_payment_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or MECHANIC not in mechanic_ids:
        return ()
    try:
        decode_protected_attack_payment(thaw_value(FrozenMap(effects[0])))
    except (TypeError, ValueError, KeyError):
        return ()
    return (CAPABILITY, "trigger.placement.apnap")


def protected_attack_payment_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(row.get("op") == OPERATION for row in nested_effects((*program.effects, *program.handlers))):
        return ()
    required = protected_attack_payment_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.cost_schema is not None or program.handlers):
        raise ValueError("Protected attack payment requires its original untargeted source-entry result")
    return required


def is_closed_protected_attack_payment_program(program):
    try:
        required = protected_attack_payment_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
