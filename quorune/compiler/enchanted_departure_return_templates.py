from __future__ import annotations

"""Compile one Aura's exact non-targeting attached-departure return."""

from dataclasses import replace

from ..ability_fragments import ability_fragment_to_dict
from ..aura import parse_enchant_line
from ..replacement.immutable import FrozenMap
from ..rules.enchanted_departure_events import (
    ENCHANTED_DEPARTURE_EVENT, ENCHANTED_DEPARTURE_VARIANT, enchanted_departure_condition,
)
from . import regex_working_set as re
from .ir_model import append_residual


_TRIGGER = re.compile(
    r"when enchanted permanent dies or is put into exile, "
    r"(?P<body>return that card to the battlefield under your control\.)",
    re.IGNORECASE,
)
_TEMPLATE = "fixed-counter-enchanted-departure-linked-return-v1"
_ENCHANT_HANDLER = "ability.static.enchant.typed.v2"
_ENCHANT = "Enchant creature or planeswalker you control"


def enchanted_departure_return_binding(material_line):
    from .fixed_public_event_trigger_bindings import _spec

    match = _TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    return _spec(
        ENCHANTED_DEPARTURE_EVENT, ENCHANTED_DEPARTURE_VARIANT, match["body"],
        _TEMPLATE, "trigger-event-normalized-zone-change",
        condition=enchanted_departure_condition(),
        capabilities=("attachment.reference.current_or_lki",),
    )


def enchanted_departure_return_template(binding, body):
    from ..rules.enchanted_departure_return import (
        ENCHANTED_DEPARTURE_RETURN_MECHANIC, enchanted_departure_return_effect,
    )

    if (
        binding.event.value != ENCHANTED_DEPARTURE_EVENT or binding.variant != ENCHANTED_DEPARTURE_VARIANT
        or binding.active_zone != "battlefield" or binding.template_id != _TEMPLATE or body != binding.body
        or FrozenMap(binding.event_condition or {}) != FrozenMap(enchanted_departure_condition())
    ):
        return None
    return (binding.template_id, (enchanted_departure_return_effect(),), None, (ENCHANTED_DEPARTURE_RETURN_MECHANIC,))


def require_enchanted_departure_restriction(nodes, residuals):
    from ..rules.enchanted_departure_return import ENCHANTED_DEPARTURE_RETURN_MECHANIC

    if not any(ENCHANTED_DEPARTURE_RETURN_MECHANIC in node.mechanics for node in nodes):
        return nodes
    expected = ability_fragment_to_dict(parse_enchant_line(_ENCHANT))
    declarations = [
        (node, handler) for node in nodes for handler in node.handlers
        if handler.get("handler_id", "").startswith("ability.static.enchant.")
    ]
    if len(declarations) == 1:
        declaration, handler = declarations[0]
        if declaration.exact and handler.get("handler_id") == _ENCHANT_HANDLER and handler.get("fragment") == expected:
            return nodes
    result = []
    for node in nodes:
        if ENCHANTED_DEPARTURE_RETURN_MECHANIC in node.mechanics:
            residual = append_residual(
                residuals, kind="trigger_source_restriction", text=node.text, span=node.span,
                reason="Enchanted departure requires its exact compiled Aura restriction",
                blockers=("controlled creature-or-planeswalker Enchant",),
            )
            node = replace(node, exact=False, lowerable=False, effects=(), residual_ids=(*node.residual_ids, residual))
        result.append(node)
    return result
