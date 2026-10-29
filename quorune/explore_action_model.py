"""Additive Explore action semantics for checkpoint and replay provenance."""

EXPLORE_ACTION_VERSION_FIELD = 'explore_action_version'


def explore_action_version(value):
    if value is None:
        return None
    if type(value) is not int or value != 1:
        raise ValueError('Unsupported Explore action version')
    return value
