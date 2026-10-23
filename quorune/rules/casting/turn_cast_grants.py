from __future__ import annotations

"""Current source permission, price decoration and canonical usage coordination."""

from copy import deepcopy
import hashlib

from ...ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ...activation_usage import cast_grant_usage_available, commit_cast_grant_usage
from ...card_program_faces import program_matches_face
from ...card_programs.admission import program_has_complete_card_program_admission
from ...object_query import object_matches_query, object_query_result
from ...util import stable_json
from ..turn_cast_grant_model import (
    TURN_CAST_GRANT_EVENT, TURN_CAST_GRANT_FIELD, TURN_CAST_GRANT_HANDLER,
    TURN_CAST_GRANT_HANDLERS,
    TurnCastGrantSpec, validate_turn_cast_grant_use,
)


def current_turn_cast_grants(host, seat, card, *, cast_type_line=None, for_land=False):
    if host.state.active_player != seat or card.owner != seat or card.object_kind != 'card':
        return ()
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
                active_zone='battlefield', event=TURN_CAST_GRANT_EVENT):
            if not any(d.get('handler_id') in TURN_CAST_GRANT_HANDLERS for d in program.handlers):
                continue
            if (host.semantic_program_is_current_trusted(program) and program_has_complete_card_program_admission(program)
                    and program_matches_face(record, program, source)
                    and cast_grant_usage_available(source, ability_id=program.key, turn_sequence=host.state.turn_sequence)):
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
            if descriptor.get('handler_id') not in TURN_CAST_GRANT_HANDLERS:
                continue
            for spec in registry.lower(descriptor, None):
                from ...turn_history import current_graveyard_incarnation_qualifies
                if (for_land and not spec.allow_land_play) or not current_graveyard_incarnation_qualifies(
                        host.state.turn_history,turn_sequence=host.state.turn_sequence,card=card,qualification=spec.qualification):
                    continue
                origins = set(spec.origins)
                if card.zone == 'library':
                    library = host.state.players[seat].zones['library']
                    eligible_origin = 'library_top' in origins and bool(library) and library[-1] == card.object_id
                else:
                    eligible_origin = card.zone in origins
                if eligible_origin and (for_land or any(object_matches_query(row, predicate) for predicate in spec.predicates_any)):
                    result.append({'source': source.object_id, 'logical': source.logical_object_id, 'program': program.key,
                        'source_ref': source.ref, 'source_label': source.printed_name, 'turn': host.state.turn_sequence,
                        'grant': spec.to_dict(), 'original_cost_id': 'normal'})
    return tuple(sorted(result, key=lambda r: (r['source'], r['program'])))


def normal_turn_grant_permission(host, seat, card):
    return any(not TurnCastGrantSpec.from_dict(use['grant']).without_mana_cost for use in current_turn_cast_grants(host, seat, card))


def free_turn_grant_base_options(host, seat, card, *, cast_type_line, enabled, has_mana_x, x_value, commander_tax):
    if not enabled or has_mana_x and x_value not in (None, 0):
        return []
    result = []
    for use in current_turn_cast_grants(host, seat, card, cast_type_line=cast_type_line):
        if not TurnCastGrantSpec.from_dict(use['grant']).without_mana_cost:
            continue
        result.append({'id': 'normal', 'kind': 'mana', 'requirements': host._mana_vector({'GENERIC': commander_tax}),
            **({'x_value_policy': 'zero'} if has_mana_x else {}), TURN_CAST_GRANT_FIELD: deepcopy(use)})
    return result


def with_turn_cast_grant_choices(host, seat, card, options, *, cast_type_line, force_without_mana_cost=False):
    if force_without_mana_cost:
        return [option for option in options if TURN_CAST_GRANT_FIELD not in option]
    providers = current_turn_cast_grants(host, seat, card, cast_type_line=cast_type_line)
    from ..zone_action_permissions import compiled_zone_cast_permission
    from ...preparation import preparation_cast_permission
    from .disturb_costs import disturb_back_cast_permission
    independent = (force_without_mana_cost or card.zone in {'hand', 'command'}
        or compiled_zone_cast_permission(host, seat, card, include_source_grants=False)
        or preparation_cast_permission(host,seat,card) or disturb_back_cast_permission(host,seat,card))
    if not providers:
        printed = current_turn_cast_grants(host, seat, card) if cast_type_line is not None else ()
        if independent or not printed:
            return [option for option in options if TURN_CAST_GRANT_FIELD not in option]
        return []
    normal = [use for use in providers if not TurnCastGrantSpec.from_dict(use['grant']).without_mana_cost]
    result = []
    for option in options:
        selected = option.get(TURN_CAST_GRANT_FIELD)
        if selected is not None:
            if selected in providers:
                result.append(option)
            continue
        own_zone_method = _has_own_graveyard_cast_method(option, card.zone)
        if independent or own_zone_method:
            result.append(option)
        if own_zone_method:
            continue
        for use in normal:
            result.append({**deepcopy(option), TURN_CAST_GRANT_FIELD: deepcopy(use)})
    return result


def _has_own_graveyard_cast_method(option, origin):
    """A zone-bearing method supplies its own permission, unlike a mana base."""
    if origin != 'graveyard':
        return False
    if option.get('source_zone') == 'graveyard' or option.get('disturb') is not None or option.get('flashback_fingerprint') is not None:
        return True
    from ...cast_lifecycles import FIXED_CAST_LIFECYCLE_CONTEXT_FIELD, FixedCastLifecycleSpec, FixedCastLifecycleKind
    value = option.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
    if value is None:
        return False
    return FixedCastLifecycleSpec.from_dict(value).kind in {
        FixedCastLifecycleKind.AFTERMATH, FixedCastLifecycleKind.ESCAPE, FixedCastLifecycleKind.JUMP_START,
        FixedCastLifecycleKind.MAYHEM, FixedCastLifecycleKind.RETRACE,
    }


def _price_id(use):
    return 'turn-cast-grant:' + hashlib.sha256(stable_json(use).encode('utf-8')).hexdigest()[:24]


def finalize_turn_cast_grant_option(option):
    use = option.get(TURN_CAST_GRANT_FIELD)
    if use is None:
        return
    from ..optional_life_cost_model import OPTIONAL_LIFE_COST_FIELD, optional_life_cost_amount
    life = option.get(OPTIONAL_LIFE_COST_FIELD)
    if life is not None:
        optional_life_cost_amount(option)
    use['original_cost_id'] = life['base_option'] if life is not None else option['id']
    validate_turn_cast_grant_use(use)
    option['id'] = _price_id(use)
    if life is not None:
        life['base_option'] = option['id']
        option['id'] += '+life-reduction:' + hashlib.sha256(stable_json(life).encode('utf-8')).hexdigest()[:24]
    option['label'] = 'Use ' + use['source_label'] + ' once: ' + str(option.get('label') or 'Cast')
    option['permission_source'] = use['source_ref']


def underlying_cast_cost_option_id(option):
    from ..optional_life_cost_model import optional_life_base_option_id
    current = optional_life_base_option_id(option)
    use = option.get(TURN_CAST_GRANT_FIELD)
    if use is None:
        return current
    validate_turn_cast_grant_use(use)
    if current != _price_id(use):
        raise ValueError('Turn casting price does not match its source identity')
    return use['original_cost_id']


def is_turn_grant_optional_cost_option(option, optional_id):
    if TURN_CAST_GRANT_FIELD not in option:
        return False
    original = underlying_cast_cost_option_id(option)
    return original == optional_id or original == 'normal+' + optional_id


def commit_turn_cast_grant_use(host, option, *, spell=None, caster=None):
    use = option.get(TURN_CAST_GRANT_FIELD)
    if use is None:
        return
    underlying_cast_cost_option_id(option)
    spec = validate_turn_cast_grant_use(use)
    if use['turn'] != host.state.turn_sequence:
        raise ValueError('Turn casting permission epoch changed')
    source = host.state.cards.get(use['source'])
    if source is not None and source.logical_object_id == use['logical']:
        commit_cast_grant_usage(source, ability_id=use['program'], turn_sequence=use['turn'])
    if spec.cast_rider != 'none':
        from ...zone_object_state import mark_cast_grant_rider
        from ..cast_grant_riders import CastGrantRiderBinding
        if spell is None:
            raise ValueError('Granted casting follow-up needs the actual stack incarnation')
        mark_cast_grant_rider(spell, CastGrantRiderBinding(spec.cast_rider, spell.logical_object_id, caster, use['source_ref']))
