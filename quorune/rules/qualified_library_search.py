"""Read current typed characteristics only within the actor's legal search window."""

from dataclasses import replace

from ..carddb_characteristics import library_card_rules_names, public_object_rules_names
from ..errors import GameRuleError
from ..library_search_model import TARGET_NAME_PERMANENT_TYPES, TARGET_NAME_SEARCH_SNAPSHOT, TargetNameLibrarySearchSpec
from ..object_query import object_query_result, object_matches_query
from ..qualified_library_search_model import QualifiedLibrarySearchSpec
from ..named_search_model import LiteralNameBattlefieldSearchSpec, decode_literal_library_search
from .current_face_keywords import library_card_keyword_abilities
from .library_search_rules import library_search_object_ids


_LIBRARY_FRONT_FACE_LAYOUTS = frozenset({"adventure", "flip", "modal_dfc", "prepare", "transform"})


def qualified_library_card_matches(spec, row):
    return (object_matches_query(row, spec.query)
            and (spec.mana_value_max is None or row.mana_value <= spec.mana_value_max)
            and (not spec.mana_values_any or row.mana_value in spec.mana_values_any)
            and (spec.stat_bound is None or spec.stat_bound.permits(
                power=row.effective_power, toughness=row.effective_toughness)))


def qualified_library_search_options(host, player, effect):
    spec = QualifiedLibrarySearchSpec.from_effect(effect)
    options = []
    for oid in library_search_object_ids(host, player, "library"):
        card = host.state.cards[oid]
        if spec.stat_bound is not None:
            record = host.card_record(card)
            if record is None or card.is_token:
                continue
            if record.faces and record.layout in _LIBRARY_FRONT_FACE_LAYOUTS:
                # Use the normal face while retaining the original library identity for live effects.
                card = replace(card, active_face=record.faces[0]["name"])
        effective = host._effective_card_data(card)
        row = object_query_result(
            card, effective, type_parts=host._type_parts(str(effective.get("type_line") or "")),
            known_to_actor=True, attached_to_ref=None,
        )
        if not qualified_library_card_matches(spec, row):
            continue
        if spec.keywords_any and library_card_keyword_abilities(host, card).isdisjoint(spec.keywords_any):
            continue
        options.append({"id": card.ref, "name": card.printed_name})
    return options


def literal_name_library_search_options(host, player, effect):
    try:
        spec = decode_literal_library_search(effect)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    battlefield = isinstance(spec, LiteralNameBattlefieldSearchSpec)
    names = {value.casefold() for value in (spec.names if battlefield else (spec.name,))}
    options = []
    for object_id in library_search_object_ids(host, player, "library"):
        card = host.state.cards[object_id]
        record = host.card_record(card)
        if record is None or names.isdisjoint(value.casefold() for value in library_card_rules_names(record)):
            continue
        if battlefield:
            data = host._effective_card_data(card)
            types = host._type_parts(str(data.get("type_line") or ""))[0]
            if not set(types).intersection(TARGET_NAME_PERMANENT_TYPES):
                continue
        options.append({"id": card.ref, "name": card.printed_name})
    return options


def _target_name_snapshot(host, player, effect, spec):
    card = host._resolve_object(player, effect["name_target"], zones={"battlefield"})
    data = host._effective_card_data(card)
    if (card.phased_out or (spec.permanent_to_battlefield and card.is_token)
            or "creature" not in host._type_parts(str(data.get("type_line") or ""))[0]):
        raise GameRuleError("Target-name search requires its current legal creature")
    names = public_object_rules_names(card, data, host.card_record(card))
    return {"reference": card.ref, "logical_object_id": card.logical_object_id, "names": list(dict.fromkeys(names))}


def bind_target_name_library_search(host, player, effect):
    try:
        spec = TargetNameLibrarySearchSpec.from_effect(effect, materialized=True)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if TARGET_NAME_SEARCH_SNAPSHOT in effect:
        raise GameRuleError("The target-name snapshot is only created as the search opens")
    return {**effect, TARGET_NAME_SEARCH_SNAPSHOT: _target_name_snapshot(host, player, effect, spec)}


def target_name_library_search_options(host, player, effect):
    try:
        spec = TargetNameLibrarySearchSpec.from_effect(effect, materialized=True)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    snapshot = effect.get(TARGET_NAME_SEARCH_SNAPSHOT)
    if snapshot != _target_name_snapshot(host, player, effect, spec):
        raise GameRuleError("The target-name search changed its original creature snapshot")
    names = frozenset(snapshot["names"])
    if not names:
        return []
    options = []
    for object_id in library_search_object_ids(host, player, "library"):
        card = host.state.cards[object_id]
        record = host.card_record(card)
        if record is None or names.isdisjoint(library_card_rules_names(record)):
            continue
        if spec.permanent_to_battlefield:
            data = host._effective_card_data(card)
            types = host._type_parts(str(data.get("type_line") or ""))[0]
            if not any(value in types for value in TARGET_NAME_PERMANENT_TYPES):
                continue
        options.append({"id": card.ref, "name": card.printed_name})
    return options
