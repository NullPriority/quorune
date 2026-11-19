from __future__ import annotations

"""Closed CardProgram lowering for fixed self counter maximums."""

from . import regex_working_set as re
from typing import Any, Mapping

from ..ability_fragments import ability_fragment_to_dict
from ..counter_maximums import CounterMaximumSpec
from ..rules.source_references import SourceReferenceSpec
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


COUNTER_MAXIMUM_TEMPLATE_ID = "static-counter-maximum-fixed-self-v1"
COUNTER_MAXIMUM_HANDLER_ID = "ability.static.counter-maximum.v1"
COUNTER_MAXIMUM_CAPABILITY_ID = "state_based.counter_maximum.fixed_self"
_COUNTER_MAXIMUM_LINE = re.compile(
    rf"(?P<subject>.+?)\s+can['’]t have more than\s+"
    rf"(?P<count>{FIXED_COUNT_PATTERN}|zero)\s+"
    r"(?P<counter>[a-z0-9+/−\- ]+?)\s+counters?\s+on\s+"
    r"(?:it|him|her)\.?",
    re.IGNORECASE,
)


def parse_fixed_self_counter_maximum(
    line: str,
    *,
    source_name: str,
) -> CounterMaximumSpec | None:
    """Compile one exact numeric CR 201.5 self-restriction sentence."""

    if type(line) is not str or type(source_name) is not str:
        return None
    match = _COUNTER_MAXIMUM_LINE.fullmatch(" ".join(line.split()))
    if match is None:
        return None
    subject = match.group("subject").strip()
    if subject.casefold() not in {"this permanent", "this creature"}:
        try:
            references = SourceReferenceSpec(source_name)
        except ValueError:
            return None
        if not references.matches(subject):
            return None
    raw_count = match.group("count")
    maximum = 0 if raw_count.casefold() == "zero" else fixed_number(raw_count)
    return CounterMaximumSpec(
        counter_name=match.group("counter"),
        maximum=maximum,
    )


def static_counter_maximum_handler(
    text: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    spec = parse_fixed_self_counter_maximum(
        text,
        source_name=source_name,
    )
    if spec is None:
        return None
    return (
        COUNTER_MAXIMUM_TEMPLATE_ID,
        {
            "handler_id": COUNTER_MAXIMUM_HANDLER_ID,
            "schema_version": 1,
            "event": "continuous",
            "fragment": ability_fragment_to_dict(spec),
        },
        COUNTER_MAXIMUM_CAPABILITY_ID,
    )


__all__ = [
    "COUNTER_MAXIMUM_CAPABILITY_ID",
    "COUNTER_MAXIMUM_HANDLER_ID",
    "COUNTER_MAXIMUM_TEMPLATE_ID",
    "parse_fixed_self_counter_maximum",
    "static_counter_maximum_handler",
]
