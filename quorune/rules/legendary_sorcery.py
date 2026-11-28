"""Apply the intrinsic casting condition of a legendary sorcery."""

from ..cast_event_type_model import (
    LEGENDARY_SORCERY_FIELD as VERSION_FIELD,
    legendary_sorcery_rule_version as validate_version,
)

CAPABILITY = "casting.legendary_sorcery.enabler"
REMINDER = "(you may cast a legendary sorcery only if you control a legendary creature or planeswalker.)"


def is_legendary_sorcery(type_line):
    from ..characteristic_evaluation import type_parts

    types, _, supertypes = type_parts(type_line)
    return "sorcery" in types and "legendary" in supertypes


def missing_legendary_enabler(host, player, row):
    from ..characteristic_evaluation import type_parts

    if "sorcery" not in row.types or "legendary" not in row.supertypes:
        return False
    for permanent in host.state.cards.values():
        if permanent.zone != "battlefield" or permanent.controller != player or permanent.phased_out:
            continue
        data = host._effective_card_data(permanent)
        types, _, supertypes = type_parts(str(data.get("type_line") or ""))
        if "legendary" in supertypes and {"creature", "planeswalker"}.intersection(types):
            return False
    return True
