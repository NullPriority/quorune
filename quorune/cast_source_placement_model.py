"""Checkpoint contracts for initial stack placement and target announcement."""

CAST_SOURCE_PLACEMENT_VERSION_FIELD='cast_source_placement_version'


def cast_source_placement_version(value):
    if value is None:return None
    if type(value) is not int or value!=1:raise ValueError('Unsupported casting source placement version')
    return value


TARGET_ANNOUNCEMENT_TIMING_VERSION_FIELD = 'target_announcement_timing_version'


def target_announcement_timing_version(value, placement):
    if value is None:
        return None
    if type(value) is not int or value != 1 or cast_source_placement_version(placement) != 1:
        raise ValueError('Target announcement timing requires version 1 and initial stack placement')
    return value


def read_target_announcement_timing_version(data):
    if TARGET_ANNOUNCEMENT_TIMING_VERSION_FIELD not in data:
        return None
    version = target_announcement_timing_version(
        data[TARGET_ANNOUNCEMENT_TIMING_VERSION_FIELD], data.get(CAST_SOURCE_PLACEMENT_VERSION_FIELD),
    )
    if version is None:
        raise ValueError('Target announcement timing version cannot be null')
    return version


def cast_placement_state_fields(placement, timing) -> dict[str, int]:
    placement = cast_source_placement_version(placement)
    timing = target_announcement_timing_version(timing, placement)
    return {
        **({CAST_SOURCE_PLACEMENT_VERSION_FIELD: placement} if placement is not None else {}),
        **({TARGET_ANNOUNCEMENT_TIMING_VERSION_FIELD: timing} if timing is not None else {}),
    }
