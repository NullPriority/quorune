"""Optional artifact/enchantment/token sacrifice through ordinary zone prices."""
from copy import deepcopy
from ...bargain_model import BARGAIN_HANDLER
from ...card_programs.admission import program_has_complete_card_program_admission
from ...declared_cast_cost_model import DECLARED_CAST_COSTS_FIELD, DeclaredCastCostFacts, DeclaredCastCostKind
from ...sacrifice_union_model import controlled_permanent_predicate
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ..casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates


def compiled_bargain_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces or card.annotations.get('copy_overrides') is not None:return None
    registry = default_cast_cost_component_registry();found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='cast.cost'):
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == BARGAIN_HANDLER:found.extend(registry.lower(descriptor, None))
    return found[0] if len(found) == 1 else None


def bargain_sacrifice_cost(spec):
    return FixedZoneChangeAdditionalCost(operation='sacrifice_one', choice_field='sacrifice_cards', schema_version=5,
        predicate=controlled_permanent_predicate(), predicate_union=spec.price_union())


def expand_bargain_options(host, seat, card, options, *, suppress_source_costs, mechanics, mandatory):
    spec = None if suppress_source_costs else compiled_bargain_spec(host, card)
    if spec is None:return options
    if mandatory or mechanics:return []
    price = bargain_sacrifice_cost(spec)
    can_pay = bool(fixed_zone_change_cost_candidates(host, actor=seat, cost=price, exclude_object_id=card.object_id))
    result = []
    for option in options:
        base = deepcopy(option);base['bargain_spec'] = spec.to_dict();base[DECLARED_CAST_COSTS_FIELD] = spec.declaration(False).to_dict();result.append(base)
        if not can_pay:continue
        paid = deepcopy(base);paid['id'] += '+bargain:' + spec.instance_id
        paid['label'] = str(option.get('label') or 'Cast') + ' with Bargain'
        paid[DECLARED_CAST_COSTS_FIELD] = spec.declaration(True).to_dict()
        paid['_additional_option_costs'] = [price.to_descriptor()];result.append(paid)
    return result


def finalize_bargain_option(host, seat, card, option, *, hint):
    raw = option.get('bargain_spec')
    if raw is None:return True
    spec = compiled_bargain_spec(host, card)
    if spec is None or spec.to_dict() != raw:return False
    try:
        facts = DeclaredCastCostFacts.from_dict(option.get(DECLARED_CAST_COSTS_FIELD))
        chosen = facts.chosen(DeclaredCastCostKind.BARGAIN, spec.instance_id)
    except (ValueError, TypeError):return False
    if facts != spec.declaration(chosen):return False
    costs = option.get('additional_costs', [])
    if costs != ([bargain_sacrifice_cost(spec).to_descriptor()] if chosen else []):return False
    if not chosen or hint:return True
    selected = option.get('selected_additional_costs', ())
    return len(selected) == 1 and selected[0].get('operation') == 'sacrifice_one' and selected[0].get('cost_position') == 0


def declared_bargain_context(option):
    if 'bargain_spec' not in option:return {}
    facts = DeclaredCastCostFacts.from_dict(option.get(DECLARED_CAST_COSTS_FIELD))
    return {DECLARED_CAST_COSTS_FIELD: facts.to_dict()}
