"""Versioned completion boundary for bounded characteristic queries."""


def characteristic_query_boundary_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError('Unsupported characteristic query boundary version')
    return value
