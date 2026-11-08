"""Versioned transform timestamps and observations with historical absence."""


TRANSFORM_EVENT = "permanent.transforms"


def transform_timestamp_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:
        raise ValueError('Unsupported transform timestamp version')
    return value


def modal_dfc_transform_version(value: int | None) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value != 1:
        raise ValueError("Unsupported modal double-faced transform version")
    return value


def transform_event_characteristics_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported transform event characteristics version")
    return value


def ordered_transform_cards(cards,apnap_order):
    order=tuple(apnap_order);cards=tuple(cards)
    if len(order)!=len(set(order)) or any(card.controller not in order for card in cards):
        raise ValueError('Transform timestamps require known active controller order')
    return tuple(card for seat in order for card in cards if card.controller==seat)
