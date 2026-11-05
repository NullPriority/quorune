"""Pinned version of the Battle Cry resolution-recipient contract."""


def battle_cry_resolution_version(value):
    if value is None:
        return None
    if type(value) is not int or value != 1:
        raise ValueError('Battle Cry resolution version must be one or absent')
    return value
