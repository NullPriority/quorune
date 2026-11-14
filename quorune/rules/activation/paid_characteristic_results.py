"""Capture the paid creature immediately before the selected sacrifice owner."""
from ...activation_paid_characteristic_model import (
    ACTIVATION_PAID_STAT_CONTEXT, ACTIVATION_PAID_STAT_MECHANIC,
    ActivationPaidCreatureFact,
)
from ...scalar_effect_amount_model import PaidCreatureStatSnapshot, ScalarAmountOrigin, scalar_amount_specs
from ...object_query import exact_numeric_characteristic
from ..activation_costs import activation_choice_candidates
from .paid_characteristic_price import activation_creature_sacrifice_choice


def activation_paid_stat_program_is_closed(program):
    from ...semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
    specs = scalar_amount_specs(program.effects)
    choice = activation_creature_sacrifice_choice(program.cost_schema)
    if (program.event != 'activate' or program.active_zone != 'battlefield'
            or program.target_schema is not None or len(program.handlers) != 1
            or ACTIVATION_PAID_STAT_MECHANIC not in program.coverage or choice is None
            or len(program.effects) != 1 or set(program.effects[0]) != {'op', 'player', 'delta'}
            or program.effects[0].get('op') != 'life' or program.effects[0].get('player') != '$controller'
            or len(specs) != 1 or specs[0].origin is not ScalarAmountOrigin.PAID_ACTIVATION_CREATURE):
        return False
    try:
        ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    except (TypeError, ValueError):
        return False
    return (ability.builtin_semantic_key == program.key and not ability.mana_ability
        and activation_creature_sacrifice_choice(ability) == choice
        and dict(ability.mana) == dict(program.cost_schema['mana'])
        and ability.tap_source == program.cost_schema['tap_source'])


def capture_activation_paid_creature_fact(host, proposal, source, ability, response):
    program = host.semantics.get(proposal.semantic_key)
    if program is None or not any(s.origin is ScalarAmountOrigin.PAID_ACTIVATION_CREATURE
            for s in scalar_amount_specs(program.effects)):
        return None
    if not activation_paid_stat_program_is_closed(program):
        raise ValueError('Paid activation creature result has no complete price and result owner')
    choice = activation_creature_sacrifice_choice(ability)
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
