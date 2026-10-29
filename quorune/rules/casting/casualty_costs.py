"""Optional ordinary sacrifice price, sealed before canonical cast payment."""
from copy import deepcopy
from ...card_programs.admission import program_has_complete_card_program_admission
from ...casualty_model import CASUALTY_HANDLER, CASUALTY_FIELD, validate_casualty_fact
from ...power_sacrifice_model import power_sacrifice_predicate
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ..casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates


def compiled_casualty_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces or card.annotations.get('copy_overrides') is not None:return None
    registry = default_cast_cost_component_registry();found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='cast.cost'):
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == CASUALTY_HANDLER:found.extend(registry.lower(descriptor, None))
    return found[0] if len(found) == 1 else None


def casualty_sacrifice_cost(spec):
    return FixedZoneChangeAdditionalCost(operation='sacrifice_one', choice_field='sacrifice_cards',
        predicate=power_sacrifice_predicate(spec.minimum_power), schema_version=4)


def expand_casualty_options(host, seat, card, options, *, suppress_source_costs, mechanics, mandatory):
    spec = None if suppress_source_costs else compiled_casualty_spec(host, card)
    if spec is None:return options
    if mandatory or mechanics:return []
    cost = casualty_sacrifice_cost(spec)
    if not fixed_zone_change_cost_candidates(host, actor=seat, cost=cost, exclude_object_id=card.object_id):return options
    result = list(options)
    for option in options:
        paid = deepcopy(option)
        paid['id'] += '+casualty:' + spec.instance_id
        paid['label'] = str(option.get('label') or 'Cast') + f' with Casualty {spec.minimum_power}'
        paid['casualty_spec'] = spec.to_dict()
        paid['_additional_option_costs'] = [cost.to_descriptor()]
        result.append(paid)
    return result


def finalize_casualty_option(host, seat, card, option, *, hint):
    raw = option.get('casualty_spec')
    if raw is None:return True
    spec = compiled_casualty_spec(host, card)
    if spec is None or spec.to_dict() != raw:return False
    if option.get('additional_costs') != [casualty_sacrifice_cost(spec).to_descriptor()]:return False
    if hint:return True
    selected = option.get('selected_additional_costs', ())
    if len(selected) != 1 or selected[0].get('operation') != 'sacrifice_one' or selected[0].get('cost_position') != 0:return False
    ref = selected[0].get('card')
    current = next((c for c in host.state.cards.values() if c.ref == ref), None)
    if current is None:return False
    option[CASUALTY_FIELD] = spec.payment_fact({'ref': current.ref, 'logical_object_id': current.logical_object_id})
    return True


def casualty_cast_context(option):
    raw = option.get(CASUALTY_FIELD)
    if raw is None:
        if 'casualty_spec' in option:raise ValueError('Paid Casualty option lost its committed sacrifice')
        return {}
    fact = validate_casualty_fact(raw)
    return {CASUALTY_FIELD: fact}
