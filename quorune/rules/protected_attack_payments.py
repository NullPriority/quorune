"""Collect resolved defender payments alongside ordinary declaration costs."""

from collections.abc import Mapping

from ..continuous_effect_model import Layer
from ..continuous_effect_state import commit_continuous_effect
from ..declaration_costs import DeclarationCost, lock_declaration_payment
from ..errors import GameRuleError
from ..life_state import LifeStateError, available_life_for_payment, pay_life_cost, prepare_life_payment
from ..util import normalize_mana_bundle
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


def offered_attack_declaration_costs(host, payer, costs):
    """Expose one independently payable option per applicable Phyrexian symbol."""
    payable_life = available_life_for_payment(host, payer) if any(cost.phyrexian_life for cost in costs) else 0
    rows = []
    for cost in costs:
        row = cost.to_dict()
        if cost.phyrexian_life is not None:
            row['methods'] = ['mana', 'life'] if payable_life >= cost.phyrexian_life else ['mana']
        rows.append(row)
    return rows


def pay_attack_declaration_costs(host, *, payer, costs, declaration, response):
    """Commit the whole locked declaration using the existing mana and life owners."""
    requirements, selected, life, methods = lock_declaration_payment(costs, declaration, payer=payer,
        phyrexian_payments=response.get('phyrexian_payments'))
    spent = normalize_mana_bundle(None)
    activations = ()
    try:
        if life:
            prepare_life_payment(host, payer, life)
        if sum(requirements.values()):
            payment = host._pay_for_cost(payer, requirements, response, spend_context='combat_declaration')
            spent, activations = payment.spent, payment.activations
        if life:
            pay_life_cost(host, payer, life)
    except LifeStateError as exc:
        raise GameRuleError(str(exc)) from exc
    return {
        'costs': [cost.cost_id for cost in selected],
        'requirements': {key: value for key, value in requirements.items() if value},
        'payment': {key: value for key, value in spent.items() if value},
        'mana_sources': [{'source': activation.get('source_ref') or activation.get('source'),
                          'bundle': activation.get('bundle')} for activation in activations],
        **({'phyrexian_payments': methods, 'life_payment': life} if methods else {}),
    }


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
