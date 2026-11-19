"""Version and compiler proof identity for stack resolution authority."""

INTRINSIC_COUNTER_PROHIBITION_TEMPLATE = "intrinsic-spell-counter-prohibition-v1"
INTRINSIC_EMPTY_SPELL_ADMISSION = "intrinsic_empty_spell_admission"
COPIED_SPELL_ENTRY_AUTHORITY_VERSION = 2


def stack_resolution_authority_version(value):
    if value is not None and (type(value) is not int or value not in {1, COPIED_SPELL_ENTRY_AUTHORITY_VERSION}):
        raise ValueError("Unsupported stack resolution authority version")
    return value
