from __future__ import annotations

"""Current source discovery and optional life choices around canonical casting."""

from collections import defaultdict
from copy import deepcopy
import hashlib
from itertools import product

from ...ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ...card_program_faces import program_matches_face
from ...card_programs.admission import program_has_complete_card_program_admission
from ...object_predicate import ObjectQuerySpec
from ...object_query import object_matches_query, object_query_result
from ..optional_life_cost_model import (
    OPTIONAL_LIFE_COST_EVENT, OPTIONAL_LIFE_COST_HANDLER, OPTIONAL_LIFE_COST_FIELD,
    OptionalLifeColorReductionSpec, optional_life_cost_amount,
)
from ...util import stable_json


def optional_life_providers(host, seat, card, *, cast_type_line):
    from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
    registry = default_cast_cost_component_registry()
    candidates = []
    for source in host._semantic_event_sources(zones={'battlefield'}):
        if source.zone != 'battlefield' or source.phased_out or source.controller != seat:
            continue
        record = host.card_record(source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
                active_zone='battlefield', event=OPTIONAL_LIFE_COST_EVENT):
            if not any(d.get('handler_id') == OPTIONAL_LIFE_COST_HANDLER for d in program.handlers):
                continue
            if (host.semantic_program_is_current_trusted(program) and program_has_complete_card_program_admission(program)
                    and program_matches_face(record, program, source)):
                candidates.append((source, program))
    if not candidates:
        return ()
    current = host._effective_static_component_key_map()
    effective = dict(host._effective_card_data(card))
    if cast_type_line is not None:
        effective['type_line'] = cast_type_line
    row = object_query_result(card, effective, type_parts=host._type_parts(str(effective.get('type_line') or '')),
        known_to_actor=True, attached_to_ref=None)
    result = []
    for source, program in candidates:
        if CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage or program.key not in current.get(source.object_id, ()):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') != OPTIONAL_LIFE_COST_HANDLER:
                continue
            for spec in registry.lower(descriptor, None):
                predicate = ObjectQuerySpec(colors_any=(spec.color,), types_any=('artifact', 'battle', 'creature', 'enchantment', 'planeswalker'))
                if object_matches_query(row, predicate):
                    result.append({'source': source.object_id, 'logical': source.logical_object_id, 'program': program.key,
                        'cost': spec.to_dict()})
    return tuple(sorted(result, key=lambda r: (r['cost']['color'], r['source'], r['program'])))


def optional_life_cost_variants(host, seat, card, options, *, cast_type_line):
    providers = optional_life_providers(host, seat, card, cast_type_line=cast_type_line)
    if not providers:
        return options
    groups = defaultdict(list)
    for provider in providers:
        groups[provider['cost']['color']].append(provider)
    colors = sorted(groups)
    maximum = max(0, host.state.players[seat].life // 2)
    # Equal-color providers have identical consequences. Enumerate counts, not subsets.
    counts = tuple(tuple(n) for n in product(*(range(min(len(groups[c]), maximum) + 1) for c in colors)) if 0 < sum(n) <= maximum)
    from ...morph import FACE_DOWN_CAST_METHODS
    result = []
    for option in options:
        result.append(option)
        if option['id'] in FACE_DOWN_CAST_METHODS:
            continue
        for count in counts:
            selected = [provider for color, number in zip(colors, count) for provider in groups[color][:number]]
            fact = {'schema_version': 1, 'base_option': option['id'], 'sources': selected}
            choice = deepcopy(option)
            choice[OPTIONAL_LIFE_COST_FIELD] = fact
            choice['id'] += '+life-reduction:' + hashlib.sha256(stable_json(fact).encode('utf-8')).hexdigest()[:24]
            choice['label'] = str(option.get('label') or 'Cast') + f'; pay {2 * len(selected)} life'
            result.append(choice)
    return result


def apply_optional_life_reduction(option):
    if not optional_life_cost_amount(option):
        return
    reductions = defaultdict(int)
    for source in option[OPTIONAL_LIFE_COST_FIELD]['sources']:
        reductions[OptionalLifeColorReductionSpec.from_dict(source['cost']).color] += 1
    applied = {}
    for color, amount in sorted(reductions.items()):
        applied[color] = min(option['requirements'].get(color, 0), amount)
        option['requirements'][color] -= applied[color]
    option.setdefault('cost_reductions', []).append({'kind': 'optional_life_colored', 'reduction': applied})


def optional_life_payment_affordable(host, seat, option, response):
    from ..casting_additional_cost_groups import fixed_life_payment_additional_cost, announced_x_life_additional_cost
    amount = optional_life_cost_amount(option)
    if not amount:
        return True
    for raw in option.get('additional_costs', ()):
        fixed = fixed_life_payment_additional_cost(raw)
        declared = announced_x_life_additional_cost(raw)
        if fixed is not None:
            amount += fixed.amount
        elif declared is not None:
            amount += declared.amount_for(option.get('announced_additional_x'))
        elif raw.get('kind') == 'life_x':
            amount += int(response.get('x', 0))
    return host.state.players[seat].life >= amount
