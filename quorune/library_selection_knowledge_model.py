"""Versioned positional knowledge after a library remainder is reordered."""


def library_selection_knowledge_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError('Unsupported library selection knowledge version')
    return value


def remainder_knowledge_viewers(*, version, player, looked_are_public, order, count):
    """None retains prior knowledge; a tuple replaces the known viewers."""
    library_selection_knowledge_version(version)
    if (type(player) is not str or not player or type(looked_are_public) is not bool
            or order not in {'chosen','random'} or type(count) is not int or count < 0):
        raise ValueError('Library remainder knowledge facts are malformed')
    if version == 1 and count > 1:
        return () if order == 'random' else (player,)
    return None if looked_are_public else (player,)
