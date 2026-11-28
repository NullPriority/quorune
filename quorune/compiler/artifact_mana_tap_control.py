from __future__ import annotations

from . import regex_working_set as re
from ..rules.artifact_mana_tap_control import (
    EVENT_CAPABILITY, MECHANIC, SOURCE_MANA_TAP_EVENT, TEMPLATE, VARIANT,
    artifact_mana_tap_control_condition, artifact_mana_tap_control_effect,
)


_TRIGGER = re.compile(
    r"Whenever an opponent taps an artifact for mana, "
    r"(?P<body>gain control of that artifact until the end of your next turn\.)", re.I,
)


def artifact_mana_tap_control_binding(material_line, binding_spec):
    match = _TRIGGER.fullmatch(" ".join(material_line.split()))
    if match is None:
        return None
    return binding_spec(SOURCE_MANA_TAP_EVENT, VARIANT, match["body"], TEMPLATE,
                        "trigger-event-normalized-public-action", condition=artifact_mana_tap_control_condition(),
                        capabilities=(EVENT_CAPABILITY,))


def artifact_mana_tap_control_result(binding, body):
    if (binding.event.value != SOURCE_MANA_TAP_EVENT or binding.variant != VARIANT or body != binding.body
            or body.casefold() != "gain control of that artifact until the end of your next turn."):
        return None
    return (TEMPLATE, (artifact_mana_tap_control_effect(),), None, (MECHANIC,)), False
