"""Versioned completion boundary for bounded characteristic queries."""

PUBLIC_ZONE_FRONT_FACE_VERSION = 2


def characteristic_query_boundary_version(value):
    if value is not None and (type(value) is not int or value not in (1, PUBLIC_ZONE_FRONT_FACE_VERSION)):
        raise ValueError('Unsupported characteristic query boundary version')
    return value
