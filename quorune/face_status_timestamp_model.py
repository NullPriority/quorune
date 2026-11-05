"""Versioned face-up/down timestamps with historical Game Record absence."""


def face_status_timestamp_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:
        raise ValueError('Unsupported face-status timestamp version')
    return value


def planned_face_up_timestamp(state):
    """Read the post-action timestamp without allocating the canonical clock."""
    version=face_status_timestamp_version(getattr(state,'face_status_timestamp_version',None))
    clock=state.timestamp_sequence
    if version==1 and (type(clock) is not int or clock<0):
        raise ValueError('Turning face up requires the canonical timestamp clock')
    return clock+1 if version==1 else None
