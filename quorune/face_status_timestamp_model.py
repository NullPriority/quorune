"""Versioned face-up/down timestamps with historical Game Record absence."""


def face_status_timestamp_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:
        raise ValueError('Unsupported face-status timestamp version')
    return value
