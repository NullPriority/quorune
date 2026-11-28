"""Compile a mandatory Behold exile only with its exact source departure."""

from __future__ import annotations

import hashlib

from . import regex_working_set as re
from .behold_cost_templates import fixed_behold_cost_leaf
from .permanent_additional_cost_nodes import permanent_additional_cost_node
from ..linked_exile_return_model import (
    LINKED_ABILITY_BINDING_PREFIX, LINKED_ABILITY_TIMING, LINKED_BEHOLD_HAND_RETURN_VERSION,
    LINKED_EXILE_RETURN_MECHANIC, LINKED_EXILE_RETURN_OPERATION, LinkedExileReturnSpec,
)
from ..rules.behold_additional_cost import FixedBeholdAdditionalCost, LINKED_BEHOLD_SCHEMA_VERSION


_PRICE = re.compile(r"As an additional cost to cast this spell, (behold [^.]+) and exile it\.", re.I)
_RETURN = re.compile(r"return the exiled card to its owner['’]s hand\.", re.I)


def linked_behold_compilers(ability, trigger, *, record, material_rows, card_types,
                            card_name, permanent, trigger_node, match_source):
    if not permanent:
        return ability, trigger
    exiling = [(line, span) for _, line, span in material_rows if re.search(r"\bexile\b", line, re.I)]
    if len(exiling) != 1:
        return ability, trigger
    price_line, price_span = exiling[0]
    match = _PRICE.fullmatch(price_line)
    leaf = fixed_behold_cost_leaf(match[1]) if match is not None else None
    if leaf is None:
        return ability, trigger
    departures = [
        (match_source(line, card_name=card_name), span)
        for _, line, span in material_rows if "return the exiled card" in line.casefold()
    ]
    if len(departures) != 1:
        return ability, trigger
    departure, departure_span = departures[0]
    if (departure is None or departure["event"].casefold() != "leaves the battlefield"
            or _RETURN.fullmatch(departure["body"]) is None):
        return ability, trigger
    seed = f"{record.oracle_id}:{price_span.line}:{departure_span.line}:behold-exile"
    pair = LINKED_ABILITY_BINDING_PREFIX + hashlib.sha256(seed.encode()).hexdigest()[:24]
    cost = FixedBeholdAdditionalCost(leaf.subtype, schema_version=LINKED_BEHOLD_SCHEMA_VERSION,
                                    linked_exile_pair=pair)
    spec = LinkedExileReturnSpec(timing=LINKED_ABILITY_TIMING, schema_version=LINKED_BEHOLD_HAND_RETURN_VERSION)
    returning = {"op": LINKED_EXILE_RETURN_OPERATION, "phase": "return", "binding_id": pair,
                 "spec": spec.to_dict()}

    def compile_ability(**values):
        if values["span"] != price_span or values["material_line"] != price_line:
            return ability(**values)
        return permanent_additional_cost_node(
            **{key: values[key] for key in ("node_id", "line", "material_line", "span",
                                            "capability_registry", "capability_profile", "residuals")},
            card_types=card_types, linked_behold_cost_schema={"additional_costs": [cost.to_descriptor()]},
        )

    def compile_trigger(**values):
        if values["span"] != departure_span:
            return trigger(**values)
        compiled = ("linked-behold-source-departure-v1", (returning,), None, (LINKED_EXILE_RETURN_MECHANIC,))
        return trigger_node(**values, effect_template=lambda _body, **_source: compiled)

    return compile_ability, compile_trigger
