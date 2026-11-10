from __future__ import annotations

from dataclasses import replace

from ..public_alternative_costs import FixedPublicAlternativeCostSpec, FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID
from .admission import program_has_complete_card_program_admission
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..rules.capabilities import targeted_exile_node_capabilities
from ..rules.casting_additional_costs import fixed_zone_change_additional_cost
from .current_view_equivalence import _same_current_source


def shadowed_reviewed_zero_price_keys(record, generated_programs, reviewed_programs):
    """Replace only source-equivalent reviewed price/exile compound views."""
    generated = tuple(generated_programs)
    shadowed = set()
    for current in generated:
        if (current.oracle_id != record.oracle_id or current.trust_level != 'trusted' or not current.capability_closure
                or current.capability_closure.get('trusted') is not True or current.event != 'resolve'
                or current.active_zone != 'stack' or current.cost_schema is not None or current.handlers):
            continue
        if current.target_schema != {'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1, 'types_any': ['creature']}:
            continue
        dependencies = targeted_exile_node_capabilities(effects=current.effects,
            target_schema=current.target_schema, mechanic_ids=current.coverage)
        if not dependencies or not set(dependencies).issubset(current.capability_dependencies):
            continue
        prices = [p for p in generated if p.oracle_id == record.oracle_id and p.trust_level == 'trusted' and p.event == 'cast.cost'
            and p.active_zone == 'all' and p.capability_closure and p.capability_closure.get('trusted') is True
            and len(p.handlers) == 1 and p.handlers[0].get('handler_id') == FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID]
        if len(prices) != 1:
            continue
        spec = FixedPublicAlternativeCostSpec.from_dict(prices[0].handlers[0]['alternative_cost'])
        if spec.condition.kind.value != 'controlled_commander' or spec.cost_text != '{0}':
            continue
        for old in reviewed_programs:
            if (old.key != current.key or old.oracle_id != record.oracle_id or old.trust_level != 'trusted'
                    or str(old.provenance.get('authored_by', '')).startswith('oracle-ir-v')):
                continue
            if any(getattr(old, field) != getattr(current, field) for field in
                    ('active_zone', 'event', 'event_condition', 'handlers', 'destination', 'requires_arbiter')):
                continue
            if any(not current.provenance.get(field) or old.provenance.get(field) != current.provenance[field]
                    or prices[0].provenance.get(field) != current.provenance[field]
                    for field in ('source_oracle_hash', 'source_rulings_hash')):
                continue
            if old.target_schema != {'zones': ['battlefield'], 'categories': ['permanent'], 'creature': True, 'count': 1}:
                continue
            if list(old.effects) != [{'op': 'exile', 'card': '$target.0'}]:
                continue
            if old.cost_schema != {'alternate_costs': [{'id': 'commander_free', 'kind': 'commander_presence',
                    'label': 'Cast without paying mana while controlling a commander', 'requirements': {},
                    'condition': {'control_commander': True}}]}:
                continue
            shadowed.add(old.key)
    return shadowed


def _single_pitch_price(record, generated):
    prices = [
        program for program in generated
        if program.oracle_id == record.oracle_id and program.trust_level == "trusted"
        and program.event == "cast.cost" and program.active_zone == "all"
        and program.capability_closure and program.capability_closure.get("trusted") is True
        and program_has_complete_card_program_admission(program) and len(program.handlers) == 1
        and program.handlers[0].get("handler_id") == FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID
    ]
    if len(prices) != 1:
        return None
    price = prices[0]
    spec = FixedPublicAlternativeCostSpec.from_dict(price.handlers[0]["alternative_cost"])
    if (spec.kind.value != "plain" or spec.condition.kind.value != "not_your_turn"
            or spec.cost_text is not None or len(spec.additional_costs) != 1):
        return None
    payment = fixed_zone_change_additional_cost(thaw_value(spec.additional_costs[0]))
    if payment is None:
        return None
    colors = payment.predicate.colors_all
    if len(colors) != 1 or payment.operation != "exile_one_from_hand" or payment.count != 1:
        return None
    if payment.predicate != ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True, colors_all=colors):
        return None
    return price, colors[0]


def _reviewed_pitch_schema(schema, color):
    if not isinstance(schema, dict) or set(schema) != {"alternate_costs"}:
        return False
    alternatives = schema["alternate_costs"]
    if not isinstance(alternatives, list) or len(alternatives) != 1 or not isinstance(alternatives[0], dict):
        return False
    return {key: value for key, value in alternatives[0].items() if key != "label"} == {
        "id": "pitch", "kind": "alternate_exile", "requirements": {},
        "condition": {"not_your_turn": True},
        "exile_from_hand": {"colors_any": [color], "exclude_source": True, "count": 1},
    }


def shadowed_reviewed_pitch_counter_keys(record, generated_programs, reviewed_programs):
    """Retire a reviewed pitch counter only when both typed parts are equivalent."""
    if record.layout != "normal" or record.faces:
        return set()
    generated = tuple(generated_programs)
    priced = _single_pitch_price(record, generated)
    if priced is None:
        return set()
    price, color = priced
    schema = {"zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1}
    reviewed = {program.key: program for program in reviewed_programs}
    result = set()
    for current in generated:
        old = reviewed.get(current.key)
        if (old is None or current.active_zone != "stack" or current.event != "resolve"
                or current.requires_arbiter or current.cost_schema is not None or current.handlers or old.handlers
                or current.target_schema != {**schema, "types_none": ["creature"]}
                or old.target_schema != {**schema, "creature": False}
                or current.effects != [{"op": "counter_stack_target", "stack": "$target.0", "counter_destination": "exile"}]
                or old.effects != [{"op": "counter_stack", "stack": "$target.0", "destination": "exile"}]
                or not _reviewed_pitch_schema(old.cost_schema, color)
                or current.event_condition != old.event_condition or current.trigger_limit != old.trigger_limit):
            continue
        if not _same_current_source(record, current, replace(old, target_schema=current.target_schema)):
            continue
        if any(price.provenance.get(field) != current.provenance.get(field)
               for field in ("source_oracle_hash", "source_rulings_hash", "face_id")):
            continue
        result.add(old.key)
    return result
