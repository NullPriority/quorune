"""Additive status-characteristic semantics with explicit historical absence."""


def face_down_characteristics_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:
        raise ValueError('Unsupported face-down characteristic version')
    return value


def face_down_characteristics_mode(host):
    return face_down_characteristics_version(getattr(getattr(host,'state',None),'face_down_characteristics_version',None))
