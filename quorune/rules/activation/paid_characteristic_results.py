"""Capture the paid creature immediately before the selected sacrifice owner."""
from dataclasses import replace

from ...activation_cost_choice import CostChoice
from ...activation_paid_characteristic_model import (
    ACTIVATION_PAID_STAT_CONTEXT, ACTIVATION_PAID_STAT_MECHANIC, ACTIVATION_PAID_STAT_CAPABILITY,
    ActivationPaidCreatureFact,
)
from ...scalar_effect_amount_model import (PaidCreatureStatSnapshot, ScalarAmountOrigin, ScalarEffectAmountSpec,
    SCALAR_AMOUNT_CAPABILITY, scalar_amount_specs)
from ...object_predicate import ObjectQuerySpec
from ...object_query import exact_numeric_characteristic
from ..activation_costs import activation_choice_candidates
from .paid_characteristic_price import activation_creature_sacrifice_choice


def _activation_price_matches(ability, program, choice):
    from ...replacement.immutable import thaw_value

    return (
        ability.builtin_semantic_key == program.key
        and not ability.mana_ability
        and activation_creature_sacrifice_choice(ability) == choice
        and dict(ability.mana) == dict(program.cost_schema['mana'])
        and ability.tap_source == program.cost_schema['tap_source']
        and thaw_value(ability.target_schema) == thaw_value(program.target_schema)
    )


def _granted_paid_toughness_is_closed(program, choice):
    expected_price = ObjectQuerySpec(
        zones=('battlefield',), controller='$actor', types_any=('creature',), known_to_actor=True,
    )
    amount = ScalarEffectAmountSpec(
        ScalarAmountOrigin.PAID_ACTIVATION_CREATURE, characteristic='toughness', schema_version=13,
    )
    return (
        program.ability_id.startswith('ability:granted:')
        and 'granted_activated_ability' in program.coverage
        and not program.handlers and program.target_schema is None
        and program.cost_schema['tap_source'] and not any(program.cost_schema['mana'].values())
        and not choice.another and choice.fixed_zone_change_cost().predicate == expected_price
        and tuple(program.effects) == ({'op': 'life', 'player': '$controller', 'delta': amount.to_dict()},)
    )


def activation_paid_stat_program_is_closed(program):
    specs = scalar_amount_specs(program.effects)
    choice = activation_creature_sacrifice_choice(program.cost_schema)
    if (program.event != 'activate' or program.active_zone != 'battlefield'
            or ACTIVATION_PAID_STAT_MECHANIC not in program.coverage or choice is None
            or not program.effects or any(e.get('op') == 'mana' for e in program.effects)
            or not specs or any(s.origin is not ScalarAmountOrigin.PAID_ACTIVATION_CREATURE for s in specs)):
        return False
    if getattr(program, 'provenance', {}).get('granted_only') is True:
        if not _granted_paid_toughness_is_closed(program, choice):
            return False
    else:
        from ...semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
        if len(program.handlers) != 1:
            return False
        try:
            ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
        except (TypeError, ValueError):
            return False
        if not _activation_price_matches(ability, program, choice):
            return False
    from ...compiler.public_query_effect_amounts import public_query_amount_program_is_closed
    return public_query_amount_program_is_closed(program,
        required_dependencies=(SCALAR_AMOUNT_CAPABILITY, ACTIVATION_PAID_STAT_CAPABILITY))


def granted_paid_activation_price_matches(program, ability):
    """Compare the current printed-fragment cost to the sealed paid-stat child."""
    if (program is None or program.provenance.get('granted_only') is not True
            or ACTIVATION_PAID_STAT_MECHANIC not in program.coverage):
        return True
    priced = activation_creature_sacrifice_choice(program.cost_schema)
    if (priced is None or not _granted_paid_toughness_is_closed(program, priced)
            or ability.choices != (CostChoice('sacrifice', card_type='creature'),)
            or ability.granting_attachment_payment is not None or ability.sorcery_speed):
        return False
    # The existing grant codec retains the simple sacrifice choice. Project its
    # exact typed equivalent for comparison and pay through the live choice.
    equivalent = replace(ability, choices=(priced,))
    return _activation_price_matches(equivalent, program, priced)


def capture_activation_paid_creature_fact(host, proposal, source, ability, response):
    program = host.semantics.get(proposal.semantic_key)
    if program is None or not any(s.origin is ScalarAmountOrigin.PAID_ACTIVATION_CREATURE
            for s in scalar_amount_specs(program.effects)):
        return None
    if not activation_paid_stat_program_is_closed(program):
        raise ValueError('Paid activation creature result has no complete price and result owner')
    if not granted_paid_activation_price_matches(program, ability):
        raise ValueError('Granted paid creature result has a different effective activation price')
    choice = (ability.choices[0] if program.provenance.get('granted_only') is True
        else activation_creature_sacrifice_choice(ability))
    if choice is None or 'cost_cards' in response and 'cost_objects' in response:
        raise ValueError('Paid activation creature selection is unavailable or ambiguous')
    refs = response.get('cost_cards', response.get('cost_objects', ()))
    if (not isinstance(refs, (list, tuple)) or len(refs) != 1 or type(refs[0]) is not str
            or refs[0] not in activation_choice_candidates(host, proposal.seat, source, choice, ability=ability)):
        raise ValueError('Paid activation requires one legal controlled creature')
    card = host._resolve_object(proposal.seat, refs[0], zones={'battlefield'})
    data = host._effective_card_data(card)
    stats = PaidCreatureStatSnapshot(card.logical_object_id,
        exact_numeric_characteristic(card, data, 'power'), exact_numeric_characteristic(card, data, 'toughness'))
    fact = ActivationPaidCreatureFact(program.key, source.logical_object_id, card.ref, card.logical_object_id, stats)
    return ACTIVATION_PAID_STAT_CONTEXT, fact.to_dict()
