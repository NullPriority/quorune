from __future__ import annotations

"""Live source authority for a printed-price Escape cast."""

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256

from ...card_programs.admission import program_has_complete_card_program_admission
from ...cast_lifecycles import (
    FIXED_CAST_LIFECYCLE_CONTEXT_FIELD, GRANTED_ESCAPE_EXILE_COUNT, GRANTED_ESCAPE_HANDLER_ID,
    GRANTED_ESCAPE_LIFECYCLE_VERSION, GRANTED_ESCAPE_USE_FIELD, FixedCastLifecycleSpec,
    escape_other_card_cost_descriptor,
)
from ...util import stable_json
from ...temporary_graveyard_escape import temporary_escape_sources
from .face_queries import current_printed_cast_mana_cost


_PRINTED_PRICE_KINDS = frozenset({"mana", "hybrid"})


def _escape_face_price(host, seat, card):
    physical = host.state.cards.get(card.object_id)
    if (physical is None or physical.zone != "graveyard" or card.zone != "graveyard"
            or physical.owner != seat or physical.object_kind != "card"
            or physical.logical_object_id != card.logical_object_id):
        return None
    if card.face_down or card.annotations.get("prospective_cast_prototype") or card.annotations.get("prospective_cast_fuse"):
        return None
    record = host.card_record(physical)
    if record is None:
        return None
    face = card.active_face
    if record.faces:
        front = str(record.faces[0]["name"])
        face = face or front
        if face != front and record.layout not in {"split", "adventure", "modal_dfc"}:
            return None
    for view in (physical, card):
        types, _, _ = host._type_parts(str(host._effective_card_data(view).get("type_line") or ""))
        if "land" in types:
            return None
    mana_cost = current_printed_cast_mana_cost(host, card, record).strip()
    return (face, mana_cost) if mana_cost else None


def _current_escape_grants(host, seat):
    programs = {
        program.key: program
        for program in host.semantics.programs_for_event("cast.cost")
        if any(descriptor.get("handler_id") == GRANTED_ESCAPE_HANDLER_ID for descriptor in program.handlers)
    }
    player = host.state.players.get(seat)
    if not programs or player is None or not player.in_game:
        return ()
    from ...semantic_runtime.cast_costs import default_cast_cost_component_registry

    current = host._effective_static_component_key_map()
    registry = default_cast_cost_component_registry()
    grants = []
    for object_id in sorted(player.zones["battlefield"]):
        source = host.state.cards.get(object_id)
        if source is None or source.zone != "battlefield" or source.controller != seat or source.phased_out:
            continue
        for key in sorted(set(current.get(object_id, ())) & programs.keys()):
            program = programs[key]
            if not (host.semantic_program_is_current_trusted(program)
                    and program_has_complete_card_program_admission(program)):
                continue
            for descriptor in program.handlers:
                if descriptor.get("handler_id") == GRANTED_ESCAPE_HANDLER_ID:
                    spec, = registry.lower(descriptor, None)
                    grants.append((source, key, spec))
    return tuple(grants)


def _available_escape_grants(host, seat, card):
    return (*_current_escape_grants(host, seat), *temporary_escape_sources(host.state, seat, card))


def compiled_granted_escape_permission(host, seat, card) -> bool:
    return _escape_face_price(host, seat, card) is not None and bool(_available_escape_grants(host, seat, card))


def _grant_use(source, key, card, *, seat, face, mana_cost, printed_id):
    return {
        "schema_version": 1, "source_object_id": source.object_id, "source_logical_object_id": source.logical_object_id,
        "source_program": key, "controller": seat, "spell_logical_object_id": card.logical_object_id,
        "face": face, "printed_mana_cost": mana_cost, "printed_cost_id": printed_id,
    }


def granted_escape_base_options(host, seat, card, printed):
    if card.zone != "graveyard":
        return []
    price = _escape_face_price(host, seat, card)
    if price is None:
        return []
    face, mana_cost = price
    options = []
    for source, key, spec in _available_escape_grants(host, seat, card):
        for base in printed:
            if base.get("kind") not in _PRINTED_PRICE_KINDS:
                continue
            use = _grant_use(source, key, card, seat=seat, face=face, mana_cost=mana_cost, printed_id=base["id"])
            identifier = sha256(stable_json(use).encode()).hexdigest()
            option = deepcopy(dict(base))
            option.update({
                "id": f"granted-escape:{identifier}", "kind": "alternate", "label": f"Escape {mana_cost}",
                "source_zone": "graveyard", GRANTED_ESCAPE_USE_FIELD: use,
                FIXED_CAST_LIFECYCLE_CONTEXT_FIELD: spec.to_dict(),
                "fixed_cast_lifecycle_fingerprint": spec.fingerprint,
                "_additional_option_costs": [*option.get("_additional_option_costs", ()),
                                             escape_other_card_cost_descriptor(GRANTED_ESCAPE_EXILE_COUNT)],
            })
            options.append(option)
    return options


def validate_granted_escape_option(host, seat, card, option, *, origin, x_value=None) -> bool:
    """Recheck the live source and printed face before any payment mutates them."""
    if GRANTED_ESCAPE_USE_FIELD not in option:
        return False
    use = option[GRANTED_ESCAPE_USE_FIELD]
    if (not isinstance(use, Mapping) or type(use.get("schema_version")) is not int
            or type(use.get("printed_cost_id")) is not str or origin != "graveyard"
            or option.get("source_zone") != "graveyard"):
        raise ValueError("Granted Escape requires its sealed source and printed price")
    price = _escape_face_price(host, seat, card)
    if price is None:
        raise ValueError("The selected Escape face is no longer payable")
    printed, _ = host._compiled_printed_cost_options(seat, card, x_value=x_value, hint=False)
    if not any(base.get("kind") in _PRINTED_PRICE_KINDS and base.get("id") == use["printed_cost_id"] for base in printed):
        raise ValueError("The selected Escape printed price changed")
    face, mana_cost = price
    expected_spec = FixedCastLifecycleSpec.from_dict(option.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD))
    if (expected_spec.schema_version != GRANTED_ESCAPE_LIFECYCLE_VERSION
            or option.get("fixed_cast_lifecycle_fingerprint") != expected_spec.fingerprint):
        raise ValueError("The granted Escape lifecycle receipt changed")
    if not any(
        spec == expected_spec and use == _grant_use(source, key, card, seat=seat, face=face,
            mana_cost=mana_cost, printed_id=use["printed_cost_id"])
        for source, key, spec in _available_escape_grants(host, seat, card)
    ):
        raise ValueError("The original Escape granting source is no longer current")
    return True
