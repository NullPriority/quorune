from __future__ import annotations

"""Admitted Emerge alternatives, chosen sacrifices and total-cost reduction."""

import math

from ...additional_cost_vocabulary import SACRIFICE_ONE_COST
from ...card_programs.admission import program_has_complete_card_program_admission
from ...emerge_model import EMERGE_FIELD, EMERGE_HANDLER, FixedManaEmergeSpec
from ...object_predicate import ObjectQuerySpec
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ..casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates


def compiled_emerge_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces or card.annotations.get('copy_overrides') is not None:
        return None
    registry = default_cast_cost_component_registry()
    found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='cast.cost'):
        descriptors = [row for row in program.handlers if row.get('handler_id') == EMERGE_HANDLER]
        if not descriptors:
            continue
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):
            return None
        for descriptor in descriptors:
            found.extend(registry.lower(descriptor, None))
    return found[0] if len(found) == 1 else None


def emerge_sacrifice_cost():
    return FixedZoneChangeAdditionalCost(operation=SACRIFICE_ONE_COST, choice_field='sacrifice_cards',
        predicate=ObjectQuerySpec(zones=('battlefield',), controller='$actor', types_all=('creature',), known_to_actor=True))


def _mana_value(host, card):
    value = host._effective_card_data(card).get('mana_value')
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0 or int(value) != value:
        raise ValueError('Emerge requires an exact current creature mana value')
    return int(value)


def emerge_base_options(host, seat, card, *, commander_tax, enabled):
    spec = compiled_emerge_spec(host, card) if enabled else None
    if spec is None:
        return []
    cost = emerge_sacrifice_cost()
    result = []
    for ref in fixed_zone_change_cost_candidates(host, actor=seat, cost=cost, exclude_object_id=card.object_id):
        victim = host._resolve_object(seat, ref, zones={'battlefield'}, controlled_only=True)
        mana = dict(spec.requirements)
        mana['GENERIC'] += commander_tax
        selection = {'spec': spec.to_dict(), 'object_id': victim.object_id, 'ref': victim.ref,
            'zone_change_counter': victim.zone_change_counter, 'mana_value': _mana_value(host, victim)}
        result.append({'id': f'emerge:{spec.instance_id}:{victim.ref}:{victim.zone_change_counter}',
            'kind': 'alternate', 'label': 'Emerge by sacrificing ' + victim.printed_name,
            'requirements': mana, EMERGE_FIELD: selection, '_additional_option_costs': [cost.to_descriptor()]})
    return result


def _selection(host, seat, card, option, *, check_mana_value):
    value = option.get(EMERGE_FIELD)
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {'spec', 'object_id', 'ref', 'zone_change_counter', 'mana_value'}
            or type(value['object_id']) is not str or type(value['ref']) is not str
            or type(value['zone_change_counter']) is not int or value['zone_change_counter'] < 0
            or type(value['mana_value']) is not int or value['mana_value'] < 0):
        raise ValueError('Emerge sacrifice selection is malformed')
    spec = FixedManaEmergeSpec.from_dict(value['spec'])
    if compiled_emerge_spec(host, card) != spec:
        raise ValueError('Emerge declaration changed before payment')
    victim = host.state.cards.get(value['object_id'])
    legal = fixed_zone_change_cost_candidates(host, actor=seat, cost=emerge_sacrifice_cost(), exclude_object_id=card.object_id)
    if (victim is None or victim.ref != value['ref'] or victim.zone_change_counter != value['zone_change_counter']
            or victim.ref not in legal):
        raise ValueError('The chosen Emerge creature is no longer available')
    if check_mana_value and _mana_value(host, victim) != value['mana_value']:
        raise ValueError('The chosen Emerge mana value changed before total-cost calculation')
    return spec, value


def reduce_emerge_total(host, seat, card, option):
    selected = _selection(host, seat, card, option, check_mana_value=True)
    if selected is None:
        return
    spec, value = selected
    before = option['requirements']['GENERIC']
    option['requirements'] = spec.reduce_total(option['requirements'], value['mana_value'])
    option.setdefault('cost_reductions', []).append({'kind': 'emerge',
        'count': before - option['requirements']['GENERIC']})


def finalize_emerge_selection(host, seat, card, option, response, choice_schema, *, hint):
    selected = _selection(host, seat, card, option, check_mana_value=True)
    if selected is None:
        return True
    _, value = selected
    if not hint:
        refs = response.get('sacrifice_cards', response.get('cost_cards'))
        if not isinstance(refs, (list, tuple)) or list(refs) != [value['ref']]:
            return False
    schema = choice_schema.get('sacrifice_cards')
    if not isinstance(schema, dict):
        return False
    schema['legal_refs'] = [value['ref']]
    return True


def validate_emerge_after_mana_payment(host, seat, card, option):
    # A chosen creature may tap for mana first, but consuming that same
    # incarnation for a mana ability cannot also pay the Emerge sacrifice.
    _selection(host, seat, card, option, check_mana_value=False)
