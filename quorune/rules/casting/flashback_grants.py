from __future__ import annotations

"""Quote and revalidate a temporary printed-price Flashback alternative."""

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256

from ...compiled_flashback import compiled_fixed_mana_flashback_spec
from ...flashback import FLASHBACK_CAST_OPTION_ID
from ...temporary_graveyard_escape import current_temporary_graveyard_grants
from ...util import stable_json
from ..target_graveyard_flashback import GRAVEYARD_FLASHBACK_STATS_FIELD
from .face_queries import graveyard_printed_cast_price, prospective_cast_card


GRANTED_FLASHBACK_OPTION_FIELD = "granted_flashback_use"
_PRINTED_BASE_KINDS = frozenset({"mana", "hybrid"})


def _flashback_price(host, seat, card):
    price = graveyard_printed_cast_price(host, seat, card)
    if price is None:
        return None
    types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
    return price if {"instant", "sorcery"}.intersection(types) else None


def _current_grants(state, seat, card):
    return current_temporary_graveyard_grants(state, seat, card, field=GRAVEYARD_FLASHBACK_STATS_FIELD)


def granted_flashback_permission(host, seat, card):
    return bool(_flashback_price(host, seat, card) is not None and _current_grants(host.state, seat, card))


def _quote_use(grant, *, seat, face, mana_cost, printed_id):
    return {
        "schema_version": 1, "controller": seat, "graveyard_logical_object_id": grant.target.logical_object_id,
        "source_stack_ref": grant.source.stack_ref, "source_program": grant.program_key,
        "turn_sequence": grant.turn_sequence, "face": face, "printed_mana_cost": mana_cost, "printed_cost_id": printed_id,
    }


def granted_flashback_base_options(host, seat, card, printed):
    grants = _current_grants(host.state, seat, card)
    price = _flashback_price(host, seat, card) if grants else None
    if price is None:
        return []
    face, mana_cost = price
    options = []
    for base in printed:
        if base.get("kind") not in _PRINTED_BASE_KINDS:
            continue
        use = _quote_use(grants[0], seat=seat, face=face, mana_cost=mana_cost, printed_id=base["id"])
        digest = sha256(stable_json(use).encode()).hexdigest()
        option = deepcopy(dict(base))
        option.update({
            "id": f"granted-flashback:{digest}", "kind": "alternate", "label": f"Flashback {mana_cost} (granted)",
            "source_zone": "graveyard", GRANTED_FLASHBACK_OPTION_FIELD: use,
        })
        options.append(option)
    return options


def is_flashback_cast_option(option):
    return option.get("id") == FLASHBACK_CAST_OPTION_ID or GRANTED_FLASHBACK_OPTION_FIELD in option


def validate_flashback_commit(host, seat, card, option, *, origin, face, x_value):
    if option.get("id") == FLASHBACK_CAST_OPTION_ID:
        current = compiled_fixed_mana_flashback_spec(host, card)
        if (origin != "graveyard" or current is None or option.get("flashback_fingerprint") != current.fingerprint
                or option.get("source_zone") != "graveyard"):
            raise ValueError("The fixed-mana Flashback contract changed before commit")
    if GRANTED_FLASHBACK_OPTION_FIELD not in option:
        return
    if origin != "graveyard" or option.get("source_zone") != "graveyard":
        raise ValueError("Granted Flashback must cast its original graveyard card")
    view = prospective_cast_card(host, card, face)
    printed, _ = host._compiled_printed_cost_options(seat, view, x_value=x_value, hint=False)
    active_options = granted_flashback_base_options(host, seat, view, printed)
    use = option[GRANTED_FLASHBACK_OPTION_FIELD]
    if (not isinstance(use, Mapping) or type(use.get("schema_version")) is not int
            or not any(quote[GRANTED_FLASHBACK_OPTION_FIELD] == use for quote in active_options)):
        raise ValueError("The original Flashback grant or chosen printed price changed")
