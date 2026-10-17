from __future__ import annotations

"""Current Conspire pair selection; existing cast owners pay its tap cost."""

from copy import deepcopy
import hashlib
from itertools import combinations
from ...card_programs.admission import program_has_complete_card_program_admission
from ...conspire_model import CONSPIRE_HANDLER, CONSPIRE_FIELD, validate_conspire_fact
from ...object_predicate import ObjectQuerySpec
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ...util import stable_json
from ..activation_costs import FixedTapActivationCost, fixed_tap_cost_candidates


def compiled_conspire_spec(host, card):
    record = host.card_record(card)
    if record is None or record.layout != 'normal' or record.faces or card.annotations.get('copy_overrides') is not None:
        return None
    registry = default_cast_cost_component_registry()
    found = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='cast.cost'):
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)):
            continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == CONSPIRE_HANDLER:
                found.extend(registry.lower(descriptor, None))
    return found[0] if len(found) == 1 else None


def conspire_candidates(host, seat, card):
    colors = set(host._effective_card_data(card).get('colors') or ())
    if not colors:
        return ()
    cost = FixedTapActivationCost(count=2, predicate=ObjectQuerySpec(zones=('battlefield',),
        controller='$actor', types_all=('creature',), tapped=False, known_to_actor=True))
    refs = set(fixed_tap_cost_candidates(host, actor=seat, source=card, cost=cost))
    return tuple(sorted((candidate for candidate in host.state.cards.values()
        if candidate.ref in refs and colors.intersection(host._effective_card_data(candidate).get('colors') or ())),
        key=lambda candidate: candidate.ref))


def expand_conspire_options(host, seat, card, options, *, suppress_source_costs, mechanics, mandatory):
    spec = None if suppress_source_costs else compiled_conspire_spec(host, card)
    if spec is None:
        return options
    if mandatory or any(str(row.get('kind') or '').casefold() != 'affinity' for row in mechanics):
        return []
    result = list(options)
    candidates = conspire_candidates(host, seat, card)
    for pair in combinations(candidates, 2):
        fact = spec.payment_fact([{'ref': current.ref, 'logical_object_id': current.logical_object_id} for current in pair])
        suffix = '+conspire:' + spec.instance_id + ':' + hashlib.sha256(stable_json(fact).encode('utf-8')).hexdigest()
        for option in options:
            paid = deepcopy(option)
            paid['id'] += suffix
            paid['label'] = str(option.get('label') or 'Cast') + ' with Conspire (' + ', '.join(c.printed_name + ' [' + c.ref + ']' for c in pair) + ')'
            paid[CONSPIRE_FIELD] = fact
            result.append(paid)
    return result


def conspire_tap_cards(host, seat, card, option):
    raw = option.get(CONSPIRE_FIELD)
    if raw is None:
        return ()
    fact = validate_conspire_fact(raw)
    spec = compiled_conspire_spec(host, card)
    if spec is None or spec.instance_id != fact['instance_id']:
        raise ValueError('Conspire source contract changed')
    candidates = {candidate.ref: candidate for candidate in conspire_candidates(host, seat, card)}
    result = []
    for row in fact['objects']:
        candidate = candidates.get(row['ref'])
        if candidate is None or candidate.logical_object_id != row['logical_object_id']:
            raise ValueError('A chosen Conspire creature is no longer available')
        result.append(candidate)
    return tuple(result)


def conspire_cast_context(option):
    raw = option.get(CONSPIRE_FIELD)
    return {CONSPIRE_FIELD: validate_conspire_fact(raw)} if raw is not None else {}


def conspire_payment_cards(host, seat, card, option, selected):
    """Compose separately selected tap resources before mana affordability."""
    try:
        conspirators = conspire_tap_cards(host, seat, card, option)
    except ValueError:
        return None
    if {candidate.object_id for candidate in selected}.intersection(candidate.object_id for candidate in conspirators):
        return None
    return [*selected, *conspirators]
