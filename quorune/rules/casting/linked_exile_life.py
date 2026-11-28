"""Quote the selected spell face only through its active source and exile link."""

from ..zone_action_permissions import compiled_zone_cast_permission
from ...errors import GameRuleError
from ...selection.exile_cast import mana_value_of_cost
from ...semantic_runtime.linked_graveyard_exile_play import controller_linked_graveyard_exile_permissions
from ...util import parse_mana_symbols
from .face_queries import current_printed_cast_mana_cost
from .library_top_life import LibraryTopLifePrice

LINKED_EXILE_LIFE_PRICE_FIELD = "linked_exile_life_price"


def linked_exile_life_cost_options(host, seat, card, response, *, face_down):
    if face_down or card.zone != "exile":
        return ()
    sources = controller_linked_graveyard_exile_permissions(host, seat, card)
    if not sources:
        return ()
    record = host.card_record(card)
    if record is None:
        return ()
    cost = current_printed_cast_mana_cost(host, card, record)
    has_x = "X" in parse_mana_symbols(cost)
    announced_x = response.get("x")
    if has_x and announced_x is not None and (type(announced_x) is not int or announced_x != 0):
        return ()
    try:
        value = mana_value_of_cost(cost)
    except GameRuleError:
        return ()
    if not value.is_integer():
        return ()
    return tuple(
        LibraryTopLifePrice(permission.source_ref, permission.source_logical_object_id,
                            permission.source_semantic_key, int(value)).cost_option(
            printed_x=has_x, field=LINKED_EXILE_LIFE_PRICE_FIELD, zone="exile",
        )
        for permission in sources
    )


def linked_exile_life_variants(host, seat, card, response, build, *, face_down):
    from ..turn_cast_grant_model import TURN_CAST_GRANT_FIELD
    bases = linked_exile_life_cost_options(host, seat, card, response, face_down=face_down)
    return tuple(
        option
        for base in bases
        for option in build(response, minimum_library_x=0, alternative_base=base)
        if TURN_CAST_GRANT_FIELD not in option.to_dict()
    )


def has_separate_exile_cast_authority(host, seat, card):
    if card.zone != "exile":
        return True
    from ...preparation import preparation_cast_permission
    from ...adventure import adventure_cast_permission
    return (
        compiled_zone_cast_permission(host, seat, card, include_linked_exile=False)
        or preparation_cast_permission(host, seat, card)
        or adventure_cast_permission(host, seat, card)
    )


def validate_linked_exile_life_price(host, seat, card, option, response, *, face_down):
    payload = LibraryTopLifePrice.from_dict(option.get(LINKED_EXILE_LIFE_PRICE_FIELD)).to_dict()
    choices = linked_exile_life_cost_options(host, seat, card, response, face_down=face_down)
    if not any(choice[LINKED_EXILE_LIFE_PRICE_FIELD] == payload for choice in choices):
        raise ValueError("The quoted linked-exile life price is no longer available")
