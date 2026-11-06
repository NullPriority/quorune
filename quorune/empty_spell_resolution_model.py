"""Version and compiler proof identity for intrinsic empty spell resolution."""

INTRINSIC_COUNTER_PROHIBITION_TEMPLATE = "intrinsic-spell-counter-prohibition-v1"
INTRINSIC_EMPTY_SPELL_ADMISSION = "intrinsic_empty_spell_admission"


def empty_spell_resolution_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported empty spell resolution version")
    return value
