"""Version and compiler proof identity for stack resolution authority."""

INTRINSIC_COUNTER_PROHIBITION_TEMPLATE = "intrinsic-spell-counter-prohibition-v1"
INTRINSIC_EMPTY_SPELL_ADMISSION = "intrinsic_empty_spell_admission"


def stack_resolution_authority_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported stack resolution authority version")
    return value
