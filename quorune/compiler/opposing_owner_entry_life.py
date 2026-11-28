"""Lower the printed nonland entry and its separate opposing-owner result."""

from . import regex_working_set as re
from ..replacement.immutable import thaw_value
from ..rules.opposing_owner_entry_life import (
    OPPOSING_OWNER_ENTRY_EVENT, OPPOSING_OWNER_ENTRY_LIFE_MECHANIC, OPPOSING_OWNER_ENTRY_VARIANT,
    opposing_owner_entry_condition, opposing_owner_entry_life_effect,
)


_TRIGGER = re.compile(
    r"Whenever a nonland permanent an opponent owns enters(?: the battlefield)? under your control, (?P<body>.+)",
    re.IGNORECASE,
)
_BODY = "they lose life equal to its mana value."
_TEMPLATE = "fixed-opposing-owner-entry-mana-value-life-v1"


def opposing_owner_entry_binding(material_line, *, spec):
    match = _TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    return spec(
        OPPOSING_OWNER_ENTRY_EVENT, OPPOSING_OWNER_ENTRY_VARIANT, match["body"],
        _TEMPLATE, "trigger-event-normalized-zone-change", condition=opposing_owner_entry_condition(),
    )


def opposing_owner_entry_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != (OPPOSING_OWNER_ENTRY_EVENT, OPPOSING_OWNER_ENTRY_VARIANT, "battlefield")
        or thaw_value(binding.event_condition) != opposing_owner_entry_condition()
        or body != binding.body or " ".join(body.split()).casefold() != _BODY
    ):
        return None
    return _TEMPLATE, (opposing_owner_entry_life_effect(),), None, (OPPOSING_OWNER_ENTRY_LIFE_MECHANIC,)
