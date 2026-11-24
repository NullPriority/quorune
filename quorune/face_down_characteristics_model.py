"""Versioned face-down characteristics and departure reveals."""


FACE_DOWN_DEPARTURE_REVEAL_VERSION = 1


def face_down_characteristics_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:
        raise ValueError('Unsupported face-down characteristic version')
    return value


def face_down_characteristics_mode(host):
    return face_down_characteristics_version(getattr(getattr(host,'state',None),'face_down_characteristics_version',None))


def face_down_departure_reveal_version(value):
    if value is not None and (type(value) is not int or value != FACE_DOWN_DEPARTURE_REVEAL_VERSION):
        raise ValueError("Unsupported face-down departure reveal version")
    return value


def reveals_face_down_owner_hand_departure(state, card, destination):
    """Bind the moving card's identity before the zone transition clears its status."""
    return (
        destination == "hand" and card.zone == "battlefield" and card.face_down and card.is_card_object
        and face_down_departure_reveal_version(getattr(state, "face_down_departure_reveal_version", None))
        == FACE_DOWN_DEPARTURE_REVEAL_VERSION
    )
