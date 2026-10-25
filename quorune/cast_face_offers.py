from __future__ import annotations

"""Execution version for Adventure face offers and its intrinsic lifecycle."""


def cast_face_offers_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError('Unsupported alternative-face offer version')
    return value


def complete_cast_face_offers(state):
    return cast_face_offers_version(getattr(state,'cast_face_offers_version',None)) is not None
