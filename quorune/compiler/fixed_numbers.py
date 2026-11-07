from __future__ import annotations


FIXED_COUNT_PATTERN = (
    r"a|one|two|three|four|five|six|seven|eight|nine|ten|\d+"
)

_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twenty": 20,
}


def fixed_number(value: str) -> int:
    """Return a closed fixed Oracle quantity; reject dynamic expressions."""

    normalized = value.casefold()
    return int(normalized) if normalized.isdigit() else _NUMBER_WORDS[normalized]


__all__ = ["FIXED_COUNT_PATTERN", "fixed_number"]
