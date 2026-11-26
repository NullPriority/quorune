from __future__ import annotations

from dataclasses import replace
import hashlib

from ..public_alternative_costs import FixedPublicAlternativeCostSpec, FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID
from .admission import program_has_complete_card_program_admission
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..rules.capabilities import targeted_exile_node_capabilities
from ..rules.casting_additional_costs import fixed_zone_change_additional_cost
from .current_view_equivalence import _same_current_source, reviewed_rulings_match


def _reviewed_zero_price_resolution_view(current):
    """Match the closed resolving result on either side of the old price view."""
    if current.target_schema == {'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1, 'types_any': ['creature']}:
        return (
            targeted_exile_node_capabilities(effects=current.effects,
                target_schema=current.target_schema, mechanic_ids=current.coverage),
            {'zones': ['battlefield'], 'categories': ['permanent'], 'creature': True, 'count': 1},
            [{'op': 'exile', 'card': '$target.0'}],
            'commander_presence', 'Cast without paying mana while controlling a commander',
        )
    from ..stack_target_change_model import OptionalStackTargetChangeSpec
    from ..rules.stack_target_change_shapes import single_stack_target_change_capabilities
    from .admission import program_has_declared_complete_source_admission

    spec = OptionalStackTargetChangeSpec(spell_or_ability=True)
    if (tuple(current.effects) == spec.effects and current.target_schema == spec.target_schema
            and program_has_declared_complete_source_admission(current)):
        return (
            single_stack_target_change_capabilities(effects=current.effects,
                target_schema=current.target_schema, mechanic_ids=current.coverage),
            spec.target_schema,
            [{'op': 'retarget_stack_item', 'stack': '$target.0'}],
            'alternate', 'Cast without paying its mana cost while you control a commander',
        )
    return None


def _reviewed_zero_price_sources_match(old, current, price):
    """Require one current compiler source and a matching or explicitly reviewed ruling revision."""
    oracle = current.provenance.get("source_oracle_hash")
    rulings = current.provenance.get("source_rulings_hash")
    if (not oracle or not rulings or old.provenance.get("source_oracle_hash") != oracle
            or price.provenance.get("source_oracle_hash") != oracle
            or price.provenance.get("source_rulings_hash") != rulings):
        return False
    return reviewed_rulings_match(old, rulings)


def shadowed_reviewed_zero_price_keys(record, generated_programs, reviewed_programs):
    """Replace source-equivalent reviewed zero-price compound views."""
    generated = tuple(generated_programs)
    shadowed = set()
    for current in generated:
        if (current.oracle_id != record.oracle_id or current.trust_level != 'trusted' or not current.capability_closure
                or current.capability_closure.get('trusted') is not True or current.event != 'resolve'
                or current.active_zone != 'stack' or current.cost_schema is not None or current.handlers):
            continue
        view = _reviewed_zero_price_resolution_view(current)
        if view is None:
            continue
        dependencies, old_schema, old_effects, kind, label = view
        if not dependencies or not set(dependencies).issubset(current.capability_dependencies):
            continue
        prices = [p for p in generated if p.oracle_id == record.oracle_id and p.trust_level == 'trusted' and p.event == 'cast.cost'
            and p.active_zone == 'all' and p.capability_closure and p.capability_closure.get('trusted') is True
            and program_has_complete_card_program_admission(p) and len(p.handlers) == 1
            and p.handlers[0].get('handler_id') == FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID]
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
            if not _reviewed_zero_price_sources_match(old, current, prices[0]):
                continue
            if old.target_schema != old_schema or list(old.effects) != old_effects:
                continue
            if old.cost_schema != {'alternate_costs': [{'id': 'commander_free', 'kind': kind,
                    'label': label, 'requirements': {}, 'condition': {'control_commander': True}}]}:
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


def _reviewed_hand_exile_evoke_pair(reviewed, record, price, spec):
    from ..rules.casting.costs import _same_reviewed_hand_exile_evoke_price

    views = [program for program in reviewed if program.oracle_id == record.oracle_id
             and program.ability_id in {'spell:front', 'trigger:evoke'}]
    oracle_hash = hashlib.sha256(record.oracle_text.encode('utf-8')).hexdigest()
    if len(views) != 2 or any(
        program.trust_level != 'trusted' or program.requires_arbiter or program.handlers
        or program.target_schema is not None or program.trigger_limit is not None
        or program.provenance.get('source_oracle_hash') != oracle_hash
        or price.provenance.get('source_oracle_hash') != oracle_hash
        or not price.provenance.get('source_rulings_hash')
        or program.provenance.get('source_rulings_hash') != price.provenance['source_rulings_hash']
        for program in views
    ):
        return set()
    by_ability = {program.ability_id: program for program in views}
    spell = by_ability.get('spell:front')
    sacrifice = by_ability.get('trigger:evoke')
    if spell is None or sacrifice is None:
        return set()
    schema = spell.cost_schema
    options = schema.get('alternate_costs') if isinstance(schema, dict) and set(schema) == {'alternate_costs'} else None
    if ((spell.active_zone, spell.event, spell.destination) != ('stack', 'resolve', 'battlefield')
            or spell.effects or spell.event_condition is not None
            or not isinstance(options, list) or len(options) != 1
            or not _same_reviewed_hand_exile_evoke_price(spec, options[0])):
        return set()
    if ((sacrifice.active_zone, sacrifice.event, sacrifice.destination) != ('battlefield', 'permanent.enter.self', None)
            or sacrifice.cost_schema is not None
            or sacrifice.event_condition != {'field': 'source_annotation.evoked', 'op': 'truthy'}
            or tuple(sacrifice.effects) != ({'op': 'sacrifice_if_present', 'card': '$source'},)):
        return set()
    return {spell.key, sacrifice.key}


def shadowed_reviewed_hand_exile_evoke_keys(record, generated_programs, reviewed_programs):
    """Replace an exact archived Evoke price and its sacrifice with the typed lifecycle."""
    from ..hand_exile_evoke_model import HAND_EXILE_EVOKE_HANDLER_ID, HandExileEvokeSpec
    from ..semantic_runtime.cast_costs import hand_exile_evoke_handler_descriptor

    if record.layout != 'normal' or record.faces or not record.is_permanent_spell:
        return set()
    prices = [program for program in generated_programs if program.oracle_id == record.oracle_id
              and program.trust_level == 'trusted' and (program.active_zone, program.event) == ('stack', 'cast.cost')
              and program.capability_closure and program.capability_closure.get('trusted') is True
              and str(program.provenance.get('authored_by', '')).startswith('oracle-ir-v')
              and len(program.handlers) == 1 and program.handlers[0].get('handler_id') == HAND_EXILE_EVOKE_HANDLER_ID
              and program_has_complete_card_program_admission(program)]
    if len(prices) != 1:
        return set()
    price, = prices
    descriptor, = price.handlers
    try:
        spec = HandExileEvokeSpec.from_dict(descriptor['evoke'])
    except (KeyError, TypeError, ValueError):
        return set()
    if descriptor != hand_exile_evoke_handler_descriptor(spec):
        return set()
    return _reviewed_hand_exile_evoke_pair(reviewed_programs, record, price, spec)
