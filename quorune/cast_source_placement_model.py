"""Additive checkpoint contract for CR601.2a source placement."""

CAST_SOURCE_PLACEMENT_VERSION_FIELD='cast_source_placement_version'


def cast_source_placement_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:raise ValueError('Unsupported casting source placement version')
    return value
