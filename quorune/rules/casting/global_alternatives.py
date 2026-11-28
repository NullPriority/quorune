from __future__ import annotations

"""Collect current source-granted prices without granting casting-zone access."""

from collections.abc import Mapping
import hashlib

from ...ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ...activation_usage import cast_grant_usage_available, commit_cast_grant_usage
from ...card_program_faces import program_matches_face
from ...card_programs.admission import program_has_complete_card_program_admission
from ...compiled_flashback import compiled_ordinary_zone_cast_permission
from ...object_query import object_matches_query, object_query_result
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ...util import stable_json
from ..global_alternative_cost_model import GLOBAL_ALTERNATIVE_EVENT, GLOBAL_ALTERNATIVE_HANDLER, GlobalAlternativeCostSpec


_PRICE_FIELD = "battlefield_alternative_cost"


def _price_id(identity):
    return "battlefield-alternative:" + hashlib.sha256(stable_json(identity).encode("utf-8")).hexdigest()[:24]


def _price_spec(identity):
    if not isinstance(identity, Mapping):
        raise ValueError("Battlefield alternative price requires its source identity")
    spec = GlobalAlternativeCostSpec.from_dict(identity.get("grant"))
    fields = {"source", "logical", "program", "grant"}
    if spec.turn_limited_scope is not None:
        fields.add("turn")
        if type(identity.get("turn")) is not int or identity["turn"] < 0:
            raise ValueError("Turn-limited battlefield alternative requires a turn")
    if spec.life_from_mana_value:
        fields.add("life")
        if type(identity.get("life")) is not int or identity["life"] < 0:
            raise ValueError("The alternative life price must match an exact mana value")
    if set(identity) != fields or any(type(identity[key]) is not str or not identity[key]
                                      for key in ("source", "logical", "program")):
        raise ValueError("Battlefield alternative price identity is malformed")
    return spec


def battlefield_alternative_life_amount(option):
    identity = option.get(_PRICE_FIELD)
    if identity is None:
        return 0
    spec = _price_spec(identity)
    return identity["life"] if spec.life_from_mana_value else 0


def _life_price_for_mana_value(value):
    from ...public_quantity_reductions import public_mana_value

    return public_mana_value({"mana_value": value})


def is_battlefield_optional_cost_option(option, optional_id):
    from .turn_cast_grants import is_turn_grant_optional_cost_option, underlying_cast_cost_option_id

    if is_turn_grant_optional_cost_option(option, optional_id):
        return True
    identity = option.get(_PRICE_FIELD)
    try:
        _price_spec(identity)
        expected = _price_id(identity)
    except (TypeError, ValueError):
        return False
    return option.get("base_cost_option") == expected and underlying_cast_cost_option_id(option) == expected + "+" + optional_id


def _source_cost_programs(host, seat, source):
    if source.zone != "battlefield" or source.phased_out or source.controller != seat:
        return
    record = host.card_record(source)
    if record is None:
        return
    programs = host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id, active_zone="battlefield", event=GLOBAL_ALTERNATIVE_EVENT,
    )
    for program in programs:
        if (any(value.get("handler_id") == GLOBAL_ALTERNATIVE_HANDLER for value in program.handlers)
                and host.semantic_program_is_current_trusted(program)
                and program_has_complete_card_program_admission(program)
                and program_matches_face(record, program, source)):
            yield program


def _cost_specs(registry, program):
    for descriptor in program.handlers:
        if descriptor.get("handler_id") == GLOBAL_ALTERNATIVE_HANDLER:
            yield from registry.lower(descriptor, None)


def _cost_query(host, card, cast_type_line):
    effective = dict(host._effective_card_data(card))
    if cast_type_line is not None:
        effective["type_line"] = cast_type_line
    row = object_query_result(
        card, effective, type_parts=host._type_parts(str(effective.get("type_line") or "")),
        known_to_actor=True, attached_to_ref=None,
    )
    return row, effective.get("mana_value")


def _matches_price(host, seat, card, source, program, spec, row, mana_value):
    if (spec.origin != "any" and spec.origin != card.zone) or not object_matches_query(row, spec.predicate):
        return False
    if spec.turn_limited_scope is None:
        return True
    if spec.life_from_mana_value and host.state.active_player != seat:
        return False
    return (cast_grant_usage_available(source, ability_id=program.key, turn_sequence=host.state.turn_sequence)
            and spec.turn_limited_scope.permits_value_and_owner(
                mana_value=mana_value, actor=seat, owner=card.owner, time_counters=source.counters.get("time", 0),
            ))


def _zone_price_authority(host, seat, card):
    from ..graveyard_cast_costs import compiled_paid_graveyard_specs

    ordinary = card.zone in {"hand", "command"} or compiled_ordinary_zone_cast_permission(host, seat, card)
    paid_graveyard = tuple(spec for spec in compiled_paid_graveyard_specs(host, seat, card) if spec.mana_cost is None)
    return ordinary, paid_graveyard


def _independent_life_price_zone(host, seat, card, *, ordinary):
    if ordinary:
        return True
    if card.zone == "library":
        from ...semantic_runtime.action_permissions import controller_library_top_spell_minimum_x

        return controller_library_top_spell_minimum_x(host, seat, card) == 0
    if card.zone == "exile":
        from ...adventure import adventure_cast_permission
        from ...preparation import preparation_cast_permission

        return bool(adventure_cast_permission(host, seat, card) or preparation_cast_permission(host, seat, card))
    return False


def battlefield_alternative_base_options(host, seat, card, *, cast_type_line, enabled, has_mana_x, x_value,
                                         commander_tax, temporary_permission):
    if not enabled or has_mana_x and x_value not in (None, 0):
        return []
    from .turn_cast_grants import free_turn_grant_base_options

    free = free_turn_grant_base_options(
        host, seat, card, cast_type_line=cast_type_line, enabled=enabled,
        has_mana_x=has_mana_x, x_value=x_value, commander_tax=commander_tax,
    )
    ordinary, paid_zone = _zone_price_authority(host, seat, card)
    legacy_exile = card.zone == "exile" and temporary_permission
    life_zone = _independent_life_price_zone(host, seat, card, ordinary=ordinary)
    if not life_zone and not legacy_exile and not paid_zone:
        return free
    from ...next_spell_costs import next_eligible_free_base_options

    for option in next_eligible_free_base_options(
        host, seat, card, cast_type_line=cast_type_line, has_mana_x=has_mana_x,
        x_value=x_value, commander_tax=commander_tax,
    ):
        if ordinary or legacy_exile:
            free.append(option)
        free.extend(spec.cost_option(option, surcharge=spec.current_surcharge(host, seat, card)) for spec in paid_zone)
    sources = [(source, program) for source in host._semantic_event_sources(zones={"battlefield"})
               for program in _source_cost_programs(host, seat, source)]
    if not sources:
        return free
    current = host._effective_static_component_key_map()
    row, mana_value = _cost_query(host, card, cast_type_line)
    registry = default_cast_cost_component_registry()
    result = list(free)
    for source, program in sources:
        if CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage or program.key not in current.get(source.object_id, ()):
            continue
        for spec in _cost_specs(registry, program):
            if not _matches_price(host, seat, card, source, program, spec, row, mana_value):
                continue
            identity = {"source": source.object_id, "logical": source.logical_object_id,
                        "program": program.key, "grant": spec.to_dict()}
            if spec.turn_limited_scope is not None:
                identity["turn"] = host.state.turn_sequence
            if spec.life_from_mana_value:
                identity["life"] = _life_price_for_mana_value(mana_value)
            requirements = dict(spec.mana)
            requirements["GENERIC"] += commander_tax
            label = (f"Pay {identity['life']} life using {source.printed_name}" if spec.life_from_mana_value
                     else "Alternative mana cost from " + source.printed_name)
            option = {"id": _price_id(identity), "kind": "alternate", "label": label, "requirements": requirements,
                      **({"x_value_policy": "zero"} if has_mana_x else {}), _PRICE_FIELD: identity}
            base_authorized = life_zone if spec.life_from_mana_value else (
                ordinary or legacy_exile and spec.turn_limited_scope is None
            )
            if base_authorized:
                result.append(option)
            result.extend(paid_spec.cost_option(option, surcharge=paid_spec.current_surcharge(host, seat, card))
                          for paid_spec in paid_zone)
    return result


def reserve_turn_limited_battlefield_cost(host, proposal, card, option, *, cast_method):
    """Consume the verified allowance at cost lock within the cast transaction."""
    identity = option.get(_PRICE_FIELD)
    if identity is None:
        return
    spec = _price_spec(identity)
    if spec.turn_limited_scope is None:
        return
    from ..cross_zone_characteristics import prospective_cross_zone_spell_type_line
    from .face_queries import selected_cast_card_view

    seat = proposal.seat
    turn = host.state.turn_sequence
    source = host.state.cards.get(identity["source"])
    if source is None or source.logical_object_id != identity["logical"] or identity["turn"] != turn:
        raise ValueError("Turn-limited alternative price source or turn changed")
    program = next((p for p in _source_cost_programs(host, seat, source) if p.key == identity["program"]), None)
    if (program is None or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or program.key not in host._effective_static_component_key_map().get(source.object_id, ())
            or spec not in _cost_specs(default_cast_cost_component_registry(), program)):
        raise ValueError("Turn-limited alternative price is no longer a current source ability")

    view = selected_cast_card_view(host, card, proposal.face, cast_method)
    ordinary, paid_zone = _zone_price_authority(host, seat, view)
    if spec.life_from_mana_value:
        ordinary = _independent_life_price_zone(host, seat, view, ordinary=ordinary)
    if not ordinary and not paid_zone:
        raise ValueError("Turn-limited alternative price requires its own zone permission")
    cast_type_line = prospective_cross_zone_spell_type_line(host, view, seat, None)
    row, mana_value = _cost_query(host, view, cast_type_line)
    if not _matches_price(host, seat, view, source, program, spec, row, mana_value):
        raise ValueError("Turn-limited alternative price no longer applies to this spell")
    if spec.life_from_mana_value and identity["life"] != _life_price_for_mana_value(mana_value):
        raise ValueError("The alternative life price no longer matches this spell")
    commit_cast_grant_usage(source, ability_id=program.key, turn_sequence=turn)
